require "test_helper"

module Console
  class SlackIntegrationsControllerTest < ActionDispatch::IntegrationTest
    setup do
      @admin = users(:acme_admin)
      post login_url, params: { email: @admin.email, password: "password123456" }
    end

    test "requires login" do
      delete logout_url
      get console_slack_integration_url
      assert_redirected_to login_path
    end

    test "requires admin" do
      delete logout_url
      member = users(:member_user)
      post login_url, params: { email: member.email, password: "password123456" }

      get console_slack_integration_url
      assert_redirected_to root_path
    end

    test "renders disconnected slack page" do
      get console_slack_integration_url

      assert_response :ok
      assert_includes response.body, "Slack"
      assert_includes response.body, "OAuth redirect URL"
      assert_includes response.body, integrations_slack_callback_path
      assert_includes response.body, "/api/webhooks/slack"
    end
  end
end
