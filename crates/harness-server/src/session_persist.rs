//! Durable blocks-mode session identity.
//!
//! Blocks mode keeps one in-memory [`crate::ThreadState`] for the pod's
//! lifetime. When a sandbox is paused/reclaimed and replaced, that state is
//! lost even though the harness's own conversation files survive on the state
//! volume (`$CENTAUR_STATE_DIR`, which the sandbox entrypoint symlinks
//! `~/.claude` / `~/.codex` onto). Persisting the harness-native session id
//! next to those files lets a fresh harness-server resume the same
//! conversation natively (`claude --resume` / codex `thread/resume` /
//! `amp threads continue`).
//!
//! Everything here is best-effort: no state dir (or any I/O failure) simply
//! means no seeding, which is today's fresh-thread behavior.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::traits::HarnessKind;

const STATE_DIR_ENV: &str = "CENTAUR_STATE_DIR";
/// Bounded directory walk when checking that a session id's files exist.
const MAX_WALK_DEPTH: usize = 4;
const MAX_WALK_ENTRIES: usize = 10_000;

#[derive(Debug, Serialize, Deserialize)]
struct PersistedSession {
    harness: String,
    harness_session_id: String,
}

fn harness_name(kind: HarnessKind) -> &'static str {
    match kind {
        HarnessKind::Codex => "codex",
        HarnessKind::ClaudeCode => "claudecode",
        HarnessKind::Amp => "amp",
    }
}

fn state_dir() -> Option<PathBuf> {
    let dir = env::var(STATE_DIR_ENV).ok()?;
    let dir = dir.trim();
    if dir.is_empty() {
        return None;
    }
    let path = PathBuf::from(dir);
    path.is_dir().then_some(path)
}

fn session_file(dir: &Path) -> PathBuf {
    dir.join("harness-server").join("blocks-session.json")
}

/// Load the persisted session id for `kind`, provided the conversation files
/// it refers to are still present on the state volume (a wiped volume must
/// not seed a dangling resume id that would fail every turn).
pub(crate) fn load(kind: HarnessKind) -> Option<String> {
    let dir = state_dir()?;
    let raw = fs::read_to_string(session_file(&dir)).ok()?;
    let persisted: PersistedSession = serde_json::from_str(&raw).ok()?;
    if persisted.harness != harness_name(kind) {
        return None;
    }
    let id = persisted.harness_session_id.trim().to_owned();
    if id.is_empty() {
        return None;
    }
    let present = match kind {
        // claude: ~/.claude/projects/<cwd-hash>/<session-id>.jsonl
        HarnessKind::ClaudeCode => files_mention_id(&dir.join("claude"), &id),
        // codex: ~/.codex/sessions/**/rollout-…-<thread-id>.jsonl
        HarnessKind::Codex => files_mention_id(&dir.join("codex"), &id),
        // amp threads live server-side; no local files to check.
        HarnessKind::Amp => true,
    };
    present.then_some(id)
}

/// Persist the session id for `kind`. Best-effort; never fails the turn.
pub(crate) fn store(kind: HarnessKind, harness_session_id: &str) {
    let Some(dir) = state_dir() else {
        return;
    };
    let file = session_file(&dir);
    if let Some(parent) = file.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let payload = PersistedSession {
        harness: harness_name(kind).to_owned(),
        harness_session_id: harness_session_id.to_owned(),
    };
    let Ok(bytes) = serde_json::to_vec_pretty(&payload) else {
        return;
    };
    // Write-then-rename so a crash mid-write can't leave a torn file.
    let tmp = file.with_extension("json.tmp");
    if fs::write(&tmp, &bytes).is_ok() {
        let _ = fs::rename(&tmp, &file);
    }
}

/// True when any file under `root` (bounded walk) has `id` in its name.
fn files_mention_id(root: &Path, id: &str) -> bool {
    let mut budget = MAX_WALK_ENTRIES;
    walk_for_id(root, id, MAX_WALK_DEPTH, &mut budget)
}

fn walk_for_id(dir: &Path, id: &str, depth: usize, budget: &mut usize) -> bool {
    let Ok(entries) = fs::read_dir(dir) else {
        return false;
    };
    for entry in entries.flatten() {
        if *budget == 0 {
            return false;
        }
        *budget -= 1;
        let name = entry.file_name();
        if name.to_string_lossy().contains(id) {
            return true;
        }
        if depth > 0 {
            let path = entry.path();
            if path.is_dir() && walk_for_id(&path, id, depth - 1, budget) {
                return true;
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with_state_dir<T>(dir: &Path, f: impl FnOnce() -> T) -> T {
        // Serialize env mutation across tests.
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = LOCK.lock().unwrap();
        unsafe { env::set_var(STATE_DIR_ENV, dir) };
        let out = f();
        unsafe { env::remove_var(STATE_DIR_ENV) };
        out
    }

    #[test]
    fn round_trips_amp_session_without_file_check() {
        let tmp = tempfile::tempdir().unwrap();
        with_state_dir(tmp.path(), || {
            store(HarnessKind::Amp, "T-123");
            assert_eq!(load(HarnessKind::Amp), Some("T-123".to_owned()));
            // A different harness must not inherit the id.
            assert_eq!(load(HarnessKind::ClaudeCode), None);
        });
    }

    #[test]
    fn claude_seed_requires_session_files_on_volume() {
        let tmp = tempfile::tempdir().unwrap();
        with_state_dir(tmp.path(), || {
            store(HarnessKind::ClaudeCode, "abc-session");
            // No ~/.claude files yet: refuse to seed a dangling resume id.
            assert_eq!(load(HarnessKind::ClaudeCode), None);
            let project_dir = tmp.path().join("claude/projects/-home-agent");
            fs::create_dir_all(&project_dir).unwrap();
            fs::write(project_dir.join("abc-session.jsonl"), b"{}").unwrap();
            assert_eq!(
                load(HarnessKind::ClaudeCode),
                Some("abc-session".to_owned())
            );
        });
    }

    #[test]
    fn missing_state_dir_is_silent() {
        let tmp = tempfile::tempdir().unwrap();
        with_state_dir(&tmp.path().join("nope"), || {
            store(HarnessKind::Amp, "T-1");
            assert_eq!(load(HarnessKind::Amp), None);
        });
    }
}
