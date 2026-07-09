class CreateSlackInstallations < ActiveRecord::Migration[8.1]
  def change
    create_table :slack_installations do |t|
      t.string :team_id, null: false
      t.string :team_name
      t.string :enterprise_id
      t.string :enterprise_name
      t.string :bot_user_id
      t.string :app_id
      t.text :bot_token
      t.text :authed_user_token
      t.jsonb :bot_scopes, null: false, default: []
      t.jsonb :user_scopes, null: false, default: []
      t.references :installed_by, foreign_key: { to_table: :users }

      t.timestamps
    end

    add_index :slack_installations, :team_id, unique: true
  end
end
