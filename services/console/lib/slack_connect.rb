require "net/http"
require "uri"

module SlackConnect
  AUTHORIZATION_ENDPOINT = "https://slack.com/oauth/v2/authorize".freeze
  TOKEN_ENDPOINT = "https://slack.com/api/oauth.v2.access".freeze
  AUTH_TEST_ENDPOINT = "https://slack.com/api/auth.test".freeze

  DEFAULT_BOT_SCOPES = %w[
    app_mentions:read
    chat:write
    channels:history
    channels:read
    files:read
    groups:history
    groups:read
    im:history
    im:read
    users:read
  ].freeze

  DEFAULT_USER_SCOPES = %w[
    channels:history
    channels:read
    files:read
    users:read
  ].freeze

  class Error < StandardError
    attr_reader :reason

    def initialize(reason)
      @reason = reason
      super(reason)
    end
  end

  module_function

  def configured?
    client_id.present? && client_secret.present?
  end

  def client_id = ConsoleEnv["SLACK_CONNECT_CLIENT_ID"].presence || ConsoleEnv["SLACK_CLIENT_ID"].presence
  def client_secret = ConsoleEnv["SLACK_CONNECT_CLIENT_SECRET"].presence || ConsoleEnv["SLACK_CLIENT_SECRET"].presence

  def bot_scopes
    env_list("SLACK_CONNECT_BOT_SCOPES", DEFAULT_BOT_SCOPES)
  end

  def user_scopes
    env_list("SLACK_CONNECT_USER_SCOPES", DEFAULT_USER_SCOPES)
  end

  def authorization_url(redirect_uri:, state:)
    uri = URI.parse(AUTHORIZATION_ENDPOINT)
    params = {
      "client_id" => client_id,
      "scope" => bot_scopes.join(","),
      "redirect_uri" => redirect_uri,
      "state" => state
    }
    # Omit user_scope entirely when no user scopes are configured (e.g.
    # SLACK_CONNECT_USER_SCOPES=none) so the install prompt asks only for the
    # bot's permissions.
    params["user_scope"] = user_scopes.join(",") if user_scopes.any?
    uri.query = URI.encode_www_form(params)
    uri.to_s
  end

  def exchange_code(code:, redirect_uri:)
    response = post_form(
      TOKEN_ENDPOINT,
      "client_id" => client_id,
      "client_secret" => client_secret,
      "code" => code.to_s,
      "redirect_uri" => redirect_uri
    )
    raise Error, response["error"].presence || "oauth_exchange_failed" unless response["ok"] == true
    response
  end

  def auth_test(token)
    response = get_json(AUTH_TEST_ENDPOINT, "Authorization" => "Bearer #{token}")
    raise Error, response["error"].presence || "auth_test_failed" unless response["ok"] == true
    response
  end

  def upsert_installation!(response, installed_by:)
    auth = response.fetch("authed_user", {})
    team = response.fetch("team", {})
    enterprise = response["enterprise"] || {}

    SlackInstallation.find_or_initialize_by(team_id: team.fetch("id")).tap do |installation|
      installation.assign_attributes(
        team_name: team["name"],
        enterprise_id: enterprise["id"],
        enterprise_name: enterprise["name"],
        bot_user_id: response["bot_user_id"],
        app_id: response["app_id"],
        bot_token: response["access_token"],
        authed_user_token: auth["access_token"],
        bot_scopes: scope_list(response["scope"]),
        user_scopes: scope_list(auth["scope"]),
        installed_by: installed_by
      )
      installation.save!
    end
  rescue KeyError
    raise Error, "missing_team_id"
  end

  def scope_list(value)
    value.to_s.split(/[,\s]+/).map(&:strip).reject(&:blank?).uniq
  end

  def env_list(suffix, fallback)
    raw = ConsoleEnv[suffix].presence
    return fallback if raw.blank?
    # Explicit opt-out: an env value cannot be distinguished from "unset" when
    # blank (blank falls back to the defaults), so "none" is the sentinel for
    # "request no scopes of this kind".
    return [] if raw.strip.casecmp("none").zero?
    scope_list(raw)
  end

  def post_form(url, params)
    uri = URI.parse(url)
    request = Net::HTTP::Post.new(uri)
    request["Accept"] = "application/json"
    request.set_form_data(params)
    parse_json(http(uri).request(request))
  end

  def get_json(url, headers = {})
    uri = URI.parse(url)
    request = Net::HTTP::Get.new(uri)
    headers.each { |key, value| request[key] = value }
    parse_json(http(uri).request(request))
  end

  def http(uri)
    Net::HTTP.new(uri.host, uri.port).tap do |client|
      client.use_ssl = uri.scheme == "https"
      client.open_timeout = 5
      client.read_timeout = 10
    end
  end

  def parse_json(response)
    JSON.parse(response.body)
  rescue JSON::ParserError
    raise Error, "invalid_json_response"
  end
end
