module Console
  class SlackIntegrationsController < ApplicationController
    layout "console"

    before_action :require_admin

    STATE_PURPOSE = :slack_connect_flow
    FLOW_TTL = 10.minutes
    FLOW_COOKIE = :slack_connect_flow

    def show
      load_status
    end

    def install
      unless SlackConnect.configured?
        return redirect_to console_slack_integration_path,
                           alert: "Slack Connect is missing client ID or client secret configuration."
      end

      nonce = SecureRandom.urlsafe_base64(32)
      state = Rails.application.message_verifier(STATE_PURPOSE).generate(
        { "nonce" => nonce, "user_id" => current_user.id },
        purpose: STATE_PURPOSE,
        expires_in: FLOW_TTL
      )
      cookies.encrypted[FLOW_COOKIE] = {
        value: { "nonce" => nonce }.to_json,
        expires: FLOW_TTL.from_now,
        httponly: true,
        same_site: :lax
      }

      redirect_to SlackConnect.authorization_url(
        redirect_uri: slack_callback_redirect_uri,
        state: state
      ), allow_other_host: true
    end

    def callback
      state = Rails.application.message_verifier(STATE_PURPOSE).verified(params[:state], purpose: STATE_PURPOSE)
      if state.nil? || state["user_id"] != current_user.id
        return redirect_to console_slack_integration_path,
                           alert: "Slack connection expired or was started from another session."
      end

      flow = read_and_clear_flow_cookie
      if flow.nil? || flow["nonce"] != state["nonce"]
        return redirect_to console_slack_integration_path,
                           alert: "Slack connection expired or was started from another browser."
      end

      if params[:error].present?
        return redirect_to console_slack_integration_path,
                           alert: "Slack connection was not approved (#{params[:error]})."
      end

      response = SlackConnect.exchange_code(code: params[:code], redirect_uri: slack_callback_redirect_uri)
      installation = SlackConnect.upsert_installation!(response, installed_by: current_user)
      redirect_to console_slack_integration_path, notice: "Connected Slack workspace #{installation.display_name}."
    rescue SlackConnect::Error => e
      redirect_to console_slack_integration_path, alert: "Slack connection failed (#{e.reason})."
    rescue ActiveRecord::RecordInvalid => e
      Rails.logger.error { "slack installation save failed: #{e.record.errors.full_messages.to_sentence}" }
      redirect_to console_slack_integration_path, alert: "Slack connection failed while saving the installation."
    end

    def check
      installation = SlackInstallation.order(updated_at: :desc).first
      if installation.nil? || installation.bot_token.blank?
        return redirect_to console_slack_integration_path, alert: "No Slack installation has been connected yet."
      end

      auth = SlackConnect.auth_test(installation.bot_token)
      installation.update!(
        team_id: auth["team_id"].presence || installation.team_id,
        team_name: auth["team"].presence || installation.team_name,
        bot_user_id: auth["user_id"].presence || installation.bot_user_id
      )
      redirect_to console_slack_integration_path, notice: "Slack bot token is healthy."
    rescue SlackConnect::Error => e
      redirect_to console_slack_integration_path, alert: "Slack bot token check failed (#{e.reason})."
    end

    def disconnect
      installation = SlackInstallation.order(updated_at: :desc).first
      if installation.nil?
        return redirect_to console_slack_integration_path, alert: "No Slack workspace is connected."
      end

      name = installation.display_name
      installation.destroy!
      redirect_to console_slack_integration_path, notice: "Disconnected Slack workspace #{name}."
    end

    private

    def load_status
      @installation = SlackInstallation.order(updated_at: :desc).first
      @configured = SlackConnect.configured?
      @bot_scopes = SlackConnect.bot_scopes
      @user_scopes = SlackConnect.user_scopes
      @callback_url = slack_callback_redirect_uri
      @event_url = URI.join(public_base_url, "/api/webhooks/slack").to_s
    end

    def slack_callback_redirect_uri
      URI.join(public_base_url, "/integrations/slack/callback").to_s
    end

    def read_and_clear_flow_cookie
      raw = cookies.encrypted[FLOW_COOKIE]
      cookies.delete(FLOW_COOKIE)
      return nil if raw.blank?
      JSON.parse(raw)
    rescue JSON::ParserError
      nil
    end
  end
end
