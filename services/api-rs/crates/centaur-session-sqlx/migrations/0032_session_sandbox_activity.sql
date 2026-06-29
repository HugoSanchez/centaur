alter table sessions
    add column if not exists sandbox_last_active_at timestamptz;

update sessions
set sandbox_last_active_at = coalesce(sandbox_last_active_at, updated_at, created_at)
where sandbox_id is not null;

create index if not exists sessions_sandbox_activity_idx
    on sessions (sandbox_last_active_at, thread_key)
    where sandbox_id is not null;
