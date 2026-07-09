module Console
  # SMB-facing "Connect Google" surface. The heavy lifting -- the consent flow,
  # PKCE, the managed refreshing BrokerCredential, and the grantable wrapping
  # secret -- already lives in Oauth::FlowsController and the token broker. This
  # controller is just a friendly status page + a Connect button that kicks off
  # the existing /oauth/:slug/start flow (with a return_to back here), plus a
  # Disconnect. The operator pre-registers the Google OauthApp (slug "google").
  class GoogleIntegrationsController < ApplicationController
    layout "console"

    before_action :require_admin

    APP_SLUG = "google".freeze

    def show
      @app = OauthApp.find_by(slug: APP_SLUG)
      @configured = @app&.enabled? && @app.client_id.present?
      @credential = @app ? @app.broker_credentials.order(updated_at: :desc).first : nil
      @allowed_scopes = Array(@app&.allowed_scopes)
      @connect_url = @app && oauth_start_path(slug: @app.slug, return_to: console_google_integration_path)
      @callback_url = @app && URI.join(public_base_url, "/oauth/#{@app.slug}/callback").to_s
    end

    def disconnect
      app = OauthApp.find_by(slug: APP_SLUG)
      credential = app&.broker_credentials&.order(updated_at: :desc)&.first
      if credential.nil?
        return redirect_to console_google_integration_path, alert: "No Google account is connected."
      end

      account = credential.provider_email.presence || credential.name
      # The wrapping secret's token_broker source references the credential, and
      # BrokerCredential's before_destroy aborts while it does -- so tear the
      # secret down first (it cascades its source, rules, and grants), then the
      # credential.
      BrokerCredential.transaction do
        credential.static_secret&.destroy!
        credential.destroy!
      end
      redirect_to console_google_integration_path, notice: "Disconnected Google account #{account}."
    rescue ActiveRecord::RecordNotDestroyed, ActiveRecord::RecordInvalid => e
      Rails.logger.error { "google disconnect failed: #{e.message}" }
      redirect_to console_google_integration_path, alert: "Could not disconnect Google. Try again."
    end
  end
end
