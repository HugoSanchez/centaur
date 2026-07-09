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

    test "disconnect removes the connected installation" do
      SlackInstallation.create!(team_id: "T0BEAC5D4H1", team_name: "Itsverso", bot_scopes: [], user_scopes: [])

      assert_difference -> { SlackInstallation.count }, -1 do
        delete disconnect_integrations_slack_url
      end

      assert_redirected_to console_slack_integration_path
      assert_match(/Disconnected Slack workspace Itsverso/, flash[:notice])
    end

    test "disconnect with nothing connected shows an alert" do
      delete disconnect_integrations_slack_url

      assert_redirected_to console_slack_integration_path
      assert_match(/No Slack workspace is connected/, flash[:alert])
    end
  end
end
