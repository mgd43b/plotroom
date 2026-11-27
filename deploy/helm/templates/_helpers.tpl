{{- define "plotroom.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{- define "plotroom.labels" -}}
app.kubernetes.io/name: plotroom
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{- define "plotroom.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "plotroom.containerSecurity" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "plotroom.volumes" -}}
- name: config
  configMap:
    name: {{ .Release.Name }}
- name: data
  persistentVolumeClaim:
    claimName: {{ .Release.Name }}-data
{{- end -}}

{{- define "plotroom.mounts" -}}
- name: config
  mountPath: /etc/plotroom
  readOnly: true
- name: data
  mountPath: /data
{{- end -}}


{{- define "plotroom.projectSecretDirectory" -}}
{{- printf "/var/run/plotroom-secrets/%s" . -}}
{{- end -}}

{{- define "plotroom.projectSecretMounts" -}}
{{- range $index, $project := keys .Values.projectSecrets | sortAlpha }}
- name: project-secrets-{{ $index }}
  mountPath: {{ include "plotroom.projectSecretDirectory" $project | quote }}
  readOnly: true
{{- end }}
{{- end -}}

{{- define "plotroom.secretProjection" -}}
- secret:
    name: {{ .reference.secretName | quote }}
    optional: false
    items:
      - key: {{ .reference.key | quote }}
        path: {{ .file | quote }}
{{- end -}}

{{- define "plotroom.projectSecretVolumes" -}}
{{- range $index, $project := keys .Values.projectSecrets | sortAlpha }}
{{- $settings := index $.Values.projectSecrets $project }}
- name: project-secrets-{{ $index }}
  projected:
    defaultMode: 0440
    sources:
      {{- with $settings.github }}
      {{- with .pat }}
      {{- include "plotroom.secretProjection" (dict "reference" . "file" "github-token") | nindent 6 }}
      {{- end }}
      {{- with .app }}
      {{- include "plotroom.secretProjection" (dict "reference" . "file" "github-app.pem") | nindent 6 }}
      {{- end }}
      {{- end }}
      {{- with $settings.embedder }}
      {{- include "plotroom.secretProjection" (dict "reference" . "file" "embedder-key") | nindent 6 }}
      {{- end }}
{{- end }}
{{- end -}}


{{- define "plotroom.backupJobName" -}}
{{- if le (len .Release.Name) 45 -}}
{{- printf "%s-backup" .Release.Name -}}
{{- else -}}
{{- printf "%s-%s-backup" (.Release.Name | trunc 35 | trimSuffix "-") (.Release.Name | sha256sum | trunc 9) -}}
{{- end -}}
{{- end -}}
