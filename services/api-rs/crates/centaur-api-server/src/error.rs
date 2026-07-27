use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use centaur_session_core::ThreadKeyError;
use centaur_session_runtime::SessionRuntimeError;
use centaur_session_sqlx::SessionStoreError;
use centaur_workflows::WorkflowRuntimeError;
use serde_json::json;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ApiError {
    #[error("{0}")]
    BadRequest(String),
    #[error("{0}")]
    Unauthorized(String),
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    MethodNotAllowed(String),
    #[error("{0}")]
    PayloadTooLarge(String),
    #[error("{0}")]
    ServiceUnavailable(String),
    /// Server-side misconfiguration or invariant failure. The message is
    /// logged but never returned to the client.
    #[error("{0}")]
    Internal(String),
    #[error(transparent)]
    Runtime(#[from] SessionRuntimeError),
    #[error(transparent)]
    Workflow(#[from] WorkflowRuntimeError),
    #[error(transparent)]
    Serialize(#[from] serde_json::Error),
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
}

impl From<ThreadKeyError> for ApiError {
    fn from(error: ThreadKeyError) -> Self {
        Self::BadRequest(error.to_string())
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = match &self {
            Self::BadRequest(_) => StatusCode::BAD_REQUEST,
            Self::Unauthorized(_) => StatusCode::UNAUTHORIZED,
            Self::NotFound(_) => StatusCode::NOT_FOUND,
            Self::MethodNotAllowed(_) => StatusCode::METHOD_NOT_ALLOWED,
            Self::PayloadTooLarge(_) => StatusCode::PAYLOAD_TOO_LARGE,
            Self::ServiceUnavailable(_) => StatusCode::SERVICE_UNAVAILABLE,
            Self::Runtime(SessionRuntimeError::BadRequest(_)) => StatusCode::BAD_REQUEST,
            Self::Runtime(SessionRuntimeError::Store(SessionStoreError::NotFound { .. })) => {
                StatusCode::NOT_FOUND
            }
            Self::Runtime(SessionRuntimeError::Store(SessionStoreError::HarnessConflict {
                ..
            })) => StatusCode::CONFLICT,
            Self::Runtime(SessionRuntimeError::Store(SessionStoreError::PersonaConflict {
                ..
            })) => StatusCode::CONFLICT,
            Self::Runtime(SessionRuntimeError::Store(
                SessionStoreError::ActiveExecutionConflict { .. },
            )) => StatusCode::CONFLICT,
            Self::Workflow(WorkflowRuntimeError::BadRequest(_)) => StatusCode::BAD_REQUEST,
            Self::Workflow(WorkflowRuntimeError::Disabled(_)) => StatusCode::FORBIDDEN,
            Self::Workflow(WorkflowRuntimeError::NotFound(_)) => StatusCode::NOT_FOUND,
            Self::Workflow(WorkflowRuntimeError::Upstream(_)) => StatusCode::BAD_GATEWAY,
            Self::Internal(_)
            | Self::Runtime(_)
            | Self::Workflow(_)
            | Self::Serialize(_)
            | Self::Sqlx(_) => StatusCode::INTERNAL_SERVER_ERROR,
        };
        // 5xx error details are server-side faults: log them for operators but
        // never echo internals (SQL text, hostnames, config refs) to clients.
        let message = if status.is_server_error() {
            tracing::error!(
                status = status.as_u16(),
                error = %error_chain(&self),
                "API request failed"
            );
            "internal server error".to_owned()
        } else {
            self.to_string()
        };
        let mut body = json!({
            "ok": false,
            "error": message,
        });
        // Structured conflict details let clients (e.g. the slackbot) recover by
        // retrying with the session's existing harness instead of parsing the
        // human-readable message.
        if let Self::Runtime(SessionRuntimeError::Store(SessionStoreError::HarnessConflict {
            existing,
            requested,
            ..
        })) = &self
        {
            body["code"] = json!("harness_conflict");
            body["existing_harness"] = json!(existing);
            body["requested_harness"] = json!(requested);
        }
        if let Self::Runtime(SessionRuntimeError::Store(
            SessionStoreError::ActiveExecutionConflict {
                existing_execution_id,
                existing_status,
                ..
            },
        )) = &self
        {
            body["code"] = json!("active_execution_conflict");
            body["existing_execution_id"] = json!(existing_execution_id);
            body["existing_status"] = json!(existing_status);
        }
        (status, Json(body)).into_response()
    }
}

/// Render an error and its full `source()` chain as a single string. Causes
/// already rendered into an ancestor's message are skipped.
pub(crate) fn error_chain(error: &dyn std::error::Error) -> String {
    let mut message = error.to_string();
    let mut source = error.source();
    while let Some(cause) = source {
        let rendered = cause.to_string();
        if !message.contains(&rendered) {
            message.push_str(": ");
            message.push_str(&rendered);
        }
        source = cause.source();
    }
    message
}

#[cfg(test)]
mod tests {
    use axum::{body::to_bytes, response::IntoResponse};
    use serde_json::Value;

    use super::*;

    #[tokio::test]
    async fn active_execution_conflict_returns_structured_409() {
        let response = ApiError::Runtime(SessionRuntimeError::Store(
            SessionStoreError::ActiveExecutionConflict {
                thread_key: "verso:thread-1".to_owned(),
                existing_execution_id: "exe_active".to_owned(),
                existing_status: "queued".to_owned(),
            },
        ))
        .into_response();

        assert_eq!(response.status(), StatusCode::CONFLICT);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let body: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(body["ok"], false);
        assert_eq!(body["code"], "active_execution_conflict");
        assert_eq!(body["existing_execution_id"], "exe_active");
        assert_eq!(body["existing_status"], "queued");
    }
}
