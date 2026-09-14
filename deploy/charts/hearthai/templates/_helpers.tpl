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
