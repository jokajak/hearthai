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

{{- define "hearthai.selectorLabels" -}}
app.kubernetes.io/name: openwebui
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end -}}

{{- define "hearthai.labels" -}}
{{ include "hearthai.selectorLabels" . }}
app.kubernetes.io/part-of: hearthai
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
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

{{- define "hearthai.litellmSelector" -}}
app.kubernetes.io/name: litellm
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "hearthai.tokenClaim" -}}
{{- default (printf "%s-litellm-token" (include "hearthai.fullname" .)) .Values.litellm.persistence.existingClaim -}}
{{- end -}}
