{{- define "hearthai.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 50 | trimSuffix "-" -}}
{{- else if contains $name .Release.Name -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version for the helm.sh/chart label. Sanitized: Flux's
reconcileStrategy: Revision stamps .Chart.Version with SemVer build metadata
(e.g. 0.1.0+<git-sha>), and '+' is illegal in a Kubernetes label value.
*/}}
{{- define "hearthai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common (non-selector) recommended labels applied to every object. Selector
labels are deliberately excluded here and kept in the per-component
selectorLabels helpers, so a workload's spec.selector.matchLabels stays a strict
subset of metadata.labels and never carries the chart or version label — both
change on upgrade and would make the immutable selector unstable. Both chart and
version are sanitized against build metadata for the reason noted above.
*/}}
{{- define "hearthai.commonLabels" -}}
helm.sh/chart: {{ include "hearthai.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: hearthai
app.kubernetes.io/version: {{ .Chart.AppVersion | replace "+" "_" | trunc 63 | trimSuffix "-" | quote }}
{{- end -}}

{{- define "hearthai.web.selectorLabels" -}}
app.kubernetes.io/name: openwebui
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end -}}

{{- define "hearthai.web.labels" -}}
{{ include "hearthai.commonLabels" . }}
{{ include "hearthai.web.selectorLabels" . }}
{{- end -}}

{{- define "hearthai.litellm.selectorLabels" -}}
app.kubernetes.io/name: litellm
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "hearthai.litellm.labels" -}}
{{ include "hearthai.commonLabels" . }}
{{ include "hearthai.litellm.selectorLabels" . }}
{{- end -}}

{{- define "hearthai.meridian.selectorLabels" -}}
app.kubernetes.io/name: meridian
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "hearthai.meridian.labels" -}}
{{ include "hearthai.commonLabels" . }}
{{ include "hearthai.meridian.selectorLabels" . }}
{{- end -}}

{{- define "hearthai.localGate.selectorLabels" -}}
app.kubernetes.io/name: local-gate
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "hearthai.localGate.labels" -}}
{{ include "hearthai.commonLabels" . }}
{{ include "hearthai.localGate.selectorLabels" . }}
{{- end -}}

{{- define "hearthai.postgres.selectorLabels" -}}
app.kubernetes.io/name: postgres
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: database
{{- end -}}

{{- define "hearthai.postgres.labels" -}}
{{ include "hearthai.commonLabels" . }}
{{ include "hearthai.postgres.selectorLabels" . }}
{{- end -}}

{{- define "hearthai.claimName" -}}
{{- default (printf "%s-web-data" (include "hearthai.fullname" .)) .Values.openwebui.persistence.existingClaim -}}
{{- end -}}

{{- define "hearthai.llmUrl" -}}
{{- if .Values.litellm.enabled -}}
{{- printf "http://%s-litellm:4000/v1" (include "hearthai.fullname" .) -}}
{{- else -}}
{{- required "llm.baseUrl is required when litellm.enabled=false" .Values.llm.baseUrl -}}
{{- end -}}
{{- end -}}

{{- define "hearthai.tokenClaim" -}}
{{- default (printf "%s-litellm-token" (include "hearthai.fullname" .)) .Values.litellm.persistence.existingClaim -}}
{{- end -}}

{{/*
Caddyfile for the local gate. The upstream address is not rendered here;
Caddy substitutes {$LOCAL_UPSTREAM_URL} from the pod environment at load time.
*/}}
{{- define "hearthai.localGate.caddyfile" -}}
{
  # No admin endpoint at all (not merely localhost-bound).
  admin off
  auto_https off
}

:11434 {
  # Answered by the gate itself, so the pod is Ready while the laptop sleeps.
  handle /_gate/healthz {
    respond "ok" 200
  }

  handle {
    reverse_proxy {$LOCAL_UPSTREAM_URL} {
      transport http {
        dial_timeout 3s
        # No pooled connections: a kept-alive connection to a laptop that has just
        # vanished would hold the request until response_header_timeout, not the dial timeout.
        keepalive off
        response_header_timeout {{ .Values.localInference.gate.responseHeaderTimeout }}
      }
      health_uri /api/version
      health_interval 10s
      health_timeout 2s
      flush_interval -1
    }
  }

  # Dial failure (502), no healthy upstream (503) and a silent upstream (504)
  # all end here and become the one fixed answer.
  handle_errors {
    header Content-Type application/json
    respond `{"error":{"type":"local_unavailable","message":"Local model unavailable: the laptop is off, asleep or off the network. This model never falls back to another provider."}}` 503
  }
}
{{- end -}}

{{/*
ai-jobs: the control plane that runs model-callable tools as one sandbox per run.
Everything below is only evaluated when aiJobs.enabled.
*/}}
{{- define "hearthai.aiJobs.name" -}}
{{- printf "%s-ai-jobs" (include "hearthai.fullname" .) -}}
{{- end -}}

{{- define "hearthai.aiJobs.selectorLabels" -}}
app.kubernetes.io/name: ai-jobs
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "hearthai.aiJobs.labels" -}}
{{ include "hearthai.commonLabels" . }}
{{ include "hearthai.aiJobs.selectorLabels" . }}
{{- end -}}

{{- define "hearthai.aiJobs.image" -}}
{{- printf "%s:%s" .Values.aiJobs.image.repository (default .Chart.AppVersion .Values.aiJobs.image.tag) -}}
{{- end -}}

{{/*
The bundled hearthmem subchart's own helpers, evaluated with the subchart's context so the
Service name and pod labels can never drift from what the subchart renders.
*/}}
{{- define "hearthai.aiJobs.hearthmemContext" -}}
{{- dict "Chart" (dict "Name" "hearthmem") "Release" .Release "Values" .Values.hearthmem | toJson -}}
{{- end -}}

{{- define "hearthai.aiJobs.hearthmemService" -}}
{{- include "hearthmem.fullname" (include "hearthai.aiJobs.hearthmemContext" . | fromJson) -}}
{{- end -}}

{{/* In-cluster URL of this release's hearthmem, written so it resolves from another namespace. */}}
{{- define "hearthai.aiJobs.hearthmemUrl" -}}
{{- printf "http://%s.%s.svc:%v" (include "hearthai.aiJobs.hearthmemService" .) .Release.Namespace .Values.hearthmem.service.port -}}
{{- end -}}

{{- define "hearthai.aiJobs.enabledProfiles" -}}
{{- $profiles := list "noop-v1" -}}
{{- if .Values.aiJobs.profiles.memoryRecall.enabled -}}
{{- $profiles = append $profiles "memory-recall-v1" -}}
{{- end -}}
{{- $profiles | toJson -}}
{{- end -}}
