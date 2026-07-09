# Bridges a connected Slack installation into the running slackbot. The OAuth
# "Connect Slack" flow stores the bot token in the console DB, but the slackbot
# reads SLACK_BOT_TOKEN from its runtime secret at boot. This writes the token
# into that secret and restarts the slackbot so it picks it up -- turning the
# manual operator hand-off into an automatic step on connect.
#
# No-op outside a cluster (KubeClient.in_cluster? is false), so connecting still
# works in local dev and tests without touching Kubernetes.
module SlackRuntime
  class Error < StandardError; end

  module_function

  # The Kubernetes client. Overridable in tests (mirrors
  # SlackDm::SyncCredential#slack_api_http); defaults to the real KubeClient.
  def client
    @client || KubeClient
  end

  def client=(value)
    @client = value
  end

  def enabled?
    client.in_cluster?
  end

  def secret_name = ConsoleEnv["SLACK_RUNTIME_SECRET"].presence || "centaur-infra-env"
  def secret_key = ConsoleEnv["SLACK_RUNTIME_SECRET_KEY"].presence || "SLACK_BOT_TOKEN"
  def slackbot_deployment = ConsoleEnv["SLACKBOT_DEPLOYMENT"].presence || "centaur-centaur-slackbotv2"

  # Writes the bot token into the slackbot's runtime secret and restarts it.
  # Returns :synced on success or :skipped when off-cluster; raises
  # SlackRuntime::Error on any Kubernetes failure.
  def sync_bot_token!(token)
    return :skipped unless enabled?
    raise Error, "no bot token to activate" if token.blank?

    client.merge_patch_secret(secret_name, { secret_key => token })
    client.restart_deployment(
      slackbot_deployment,
      { "centaur.dev/slack-token-synced-at" => Time.now.utc.iso8601 }
    )
    :synced
  rescue KubeClient::Error => e
    raise Error, e.message
  end
end
