class SlackInstallation < ApplicationRecord
  belongs_to :installed_by, class_name: "User", optional: true

  encrypts :bot_token
  encrypts :authed_user_token

  validates :team_id, presence: true, uniqueness: true
  validate :bot_scopes_are_strings
  validate :user_scopes_are_strings

  def display_name
    team_name.presence || team_id
  end

  private

  def bot_scopes_are_strings
    errors.add(:bot_scopes, "must be an array of strings") unless string_array?(bot_scopes)
  end

  def user_scopes_are_strings
    errors.add(:user_scopes, "must be an array of strings") unless string_array?(user_scopes)
  end

  def string_array?(value)
    value.is_a?(Array) && value.all? { |entry| entry.is_a?(String) }
  end
end
