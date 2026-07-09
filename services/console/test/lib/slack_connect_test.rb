require "test_helper"

class SlackConnectTest < ActiveSupport::TestCase
  SCOPE_VARS = %w[
    CENTAUR_CONSOLE_SLACK_CONNECT_BOT_SCOPES
    CENTAUR_CONSOLE_SLACK_CONNECT_USER_SCOPES
    IRON_CONTROL_SLACK_CONNECT_BOT_SCOPES
    IRON_CONTROL_SLACK_CONNECT_USER_SCOPES
  ].freeze

  setup do
    @saved = SCOPE_VARS.index_with { |name| ENV[name] }
    SCOPE_VARS.each { |name| ENV.delete(name) }
  end

  teardown do
    @saved.each { |name, value| value.nil? ? ENV.delete(name) : ENV[name] = value }
  end

  test "scopes default when env unset" do
    assert_equal SlackConnect::DEFAULT_BOT_SCOPES, SlackConnect.bot_scopes
    assert_equal SlackConnect::DEFAULT_USER_SCOPES, SlackConnect.user_scopes
  end

  test "env overrides narrow the scope lists" do
    ENV["CENTAUR_CONSOLE_SLACK_CONNECT_BOT_SCOPES"] = "app_mentions:read, chat:write"
    assert_equal %w[app_mentions:read chat:write], SlackConnect.bot_scopes
  end

  test "none sentinel empties user scopes" do
    ENV["CENTAUR_CONSOLE_SLACK_CONNECT_USER_SCOPES"] = "none"
    assert_equal [], SlackConnect.user_scopes
  end

  test "authorization_url omits user_scope when user scopes are none" do
    ENV["CENTAUR_CONSOLE_SLACK_CONNECT_USER_SCOPES"] = "none"
    url = SlackConnect.authorization_url(redirect_uri: "https://example.test/cb", state: "s")
    query = Rack::Utils.parse_query(URI.parse(url).query)
    assert_nil query["user_scope"]
    assert_equal SlackConnect::DEFAULT_BOT_SCOPES.join(","), query["scope"]
  end

  test "authorization_url includes user_scope by default" do
    url = SlackConnect.authorization_url(redirect_uri: "https://example.test/cb", state: "s")
    query = Rack::Utils.parse_query(URI.parse(url).query)
    assert_equal SlackConnect::DEFAULT_USER_SCOPES.join(","), query["user_scope"]
  end
end
