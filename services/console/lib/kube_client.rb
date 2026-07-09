require "net/http"
require "uri"
require "json"

# Minimal in-cluster Kubernetes client. Deliberately dependency-free (stdlib
# net/http, mirroring SlackConnect) rather than pulling in a kube gem, because
# the console only needs two narrow operations: merge-patch a Secret and trigger
# a Deployment rollout restart. Used by SlackRuntime to activate a connected
# Slack bot token on the running slackbot.
#
# All calls use the pod's mounted ServiceAccount token + CA. Outside a cluster
# (local dev, tests) `in_cluster?` is false and callers skip entirely.
module KubeClient
  TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token".freeze
  CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt".freeze
  NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace".freeze

  class Error < StandardError; end

  module_function

  def in_cluster?
    File.exist?(TOKEN_PATH) && ENV["KUBERNETES_SERVICE_HOST"].present?
  end

  def namespace
    ConsoleEnv["POD_NAMESPACE"].presence ||
      (File.read(NAMESPACE_PATH).strip if File.exist?(NAMESPACE_PATH)) ||
      "default"
  end

  # Merge `string_data` (a plain Hash of key => value) into a Secret. The API
  # server base64-encodes stringData into data on write, so callers pass raw
  # values. Mirrors `kubectl patch secret --type merge -p '{"stringData":{...}}'`.
  def merge_patch_secret(name, string_data)
    patch("/api/v1/namespaces/#{namespace}/secrets/#{name}",
          { "stringData" => string_data },
          "application/merge-patch+json")
  end

  # Trigger a rollout restart by stamping pod-template annotations, the same
  # mechanism `kubectl rollout restart` uses.
  def restart_deployment(name, annotations)
    patch("/apis/apps/v1/namespaces/#{namespace}/deployments/#{name}",
          { "spec" => { "template" => { "metadata" => { "annotations" => annotations } } } },
          "application/strategic-merge-patch+json")
  end

  def patch(path, body, content_type)
    uri = URI.join(api_base, path)
    request = Net::HTTP::Patch.new(uri)
    request["Authorization"] = "Bearer #{token}"
    request["Content-Type"] = content_type
    request["Accept"] = "application/json"
    request.body = JSON.dump(body)

    response = http(uri).request(request)
    unless response.code.to_i.between?(200, 299)
      raise Error, "kube PATCH #{path} -> HTTP #{response.code}: #{response.body}"
    end
    parse(response.body)
  rescue SystemCallError, Timeout::Error, OpenSSL::SSL::SSLError => e
    raise Error, "kube PATCH #{path} failed: #{e.class}: #{e.message}"
  end

  def api_base
    host = ENV.fetch("KUBERNETES_SERVICE_HOST")
    port = ENV["KUBERNETES_SERVICE_PORT"].presence || "443"
    "https://#{host}:#{port}"
  end

  def token = File.read(TOKEN_PATH).strip

  def http(uri)
    Net::HTTP.new(uri.host, uri.port).tap do |client|
      client.use_ssl = true
      client.open_timeout = 5
      client.read_timeout = 10
      if File.exist?(CA_PATH)
        client.ca_file = CA_PATH
        client.verify_mode = OpenSSL::SSL::VERIFY_PEER
      end
    end
  end

  def parse(body)
    JSON.parse(body)
  rescue JSON::ParserError
    {}
  end
end
