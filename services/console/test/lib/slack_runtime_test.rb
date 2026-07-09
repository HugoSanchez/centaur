require "test_helper"

class SlackRuntimeTest < ActiveSupport::TestCase
  # Injected in place of KubeClient (see SlackRuntime.client). Records calls so
  # tests can assert what was written/restarted without a real cluster.
  class FakeKube
    attr_reader :calls

    def initialize(in_cluster: true, fail_with: nil)
      @in_cluster = in_cluster
      @fail_with = fail_with
      @calls = []
    end

    def in_cluster? = @in_cluster

    def merge_patch_secret(name, data)
      raise @fail_with if @fail_with
      @calls << [ :secret, name, data ]
      {}
    end

    def restart_deployment(name, annotations)
      @calls << [ :restart, name, annotations.keys ]
      {}
    end
  end

  teardown { SlackRuntime.client = nil }

  test "is a no-op off-cluster so dev and tests are unaffected" do
    SlackRuntime.client = FakeKube.new(in_cluster: false)
    assert_equal :skipped, SlackRuntime.sync_bot_token!("xoxb-abc")
  end

  test "writes the token into the secret and restarts the slackbot" do
    kube = FakeKube.new
    SlackRuntime.client = kube

    assert_equal :synced, SlackRuntime.sync_bot_token!("xoxb-abc")
    assert_equal [ :secret, "centaur-infra-env", { "SLACK_BOT_TOKEN" => "xoxb-abc" } ], kube.calls[0]
    assert_equal [ :restart, "centaur-centaur-slackbotv2", [ "centaur.dev/slack-token-synced-at" ] ], kube.calls[1]
  end

  test "rejects a blank token when enabled" do
    SlackRuntime.client = FakeKube.new
    assert_raises(SlackRuntime::Error) { SlackRuntime.sync_bot_token!("") }
  end

  test "wraps Kubernetes failures as SlackRuntime::Error" do
    SlackRuntime.client = FakeKube.new(fail_with: KubeClient::Error.new("boom"))

    err = assert_raises(SlackRuntime::Error) { SlackRuntime.sync_bot_token!("xoxb-abc") }
    assert_match "boom", err.message
  end
end
