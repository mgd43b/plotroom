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


{{- define "plotroom.backupJobName" -}}
{{- if le (len .Release.Name) 45 -}}
{{- printf "%s-backup" .Release.Name -}}
{{- else -}}
{{- printf "%s-%s-backup" (.Release.Name | trunc 35 | trimSuffix "-") (.Release.Name | sha256sum | trunc 9) -}}
{{- end -}}
{{- end -}}
