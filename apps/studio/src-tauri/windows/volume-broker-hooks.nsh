; Capture this while NSIS is processing the included hook file. Expanding
; __FILEDIR__ inside a hook macro would point at Tauri's generated staging
; directory instead of this source directory.
!define LR_BROKER_PAYLOAD "${__FILEDIR__}\payload\lyricrail-volume-broker.exe"

!macro LR_BROKER_REQUIRE_SUCCESS Operation
  Pop $0
  Pop $1
  ${If} $0 != 0
    DetailPrint "LyricRail Volume Broker ${Operation} failed (exit $0): $1"
    MessageBox MB_ICONSTOP|MB_OK "LyricRail Volume Broker ${Operation} failed. Studio installation cannot continue safely."
    Abort
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREINSTALL
  ; Stop an existing broker before its image is replaced during an upgrade.
  nsExec::ExecToStack '"$SYSDIR\sc.exe" stop "LyricRailVolumeBroker"'
  Pop $0
  Pop $1
  Sleep 1000
!macroend

!macro NSIS_HOOK_POSTINSTALL
  SetOutPath "$INSTDIR"
  File /oname=lyricrail-volume-broker.exe "${LR_BROKER_PAYLOAD}"

  ; `create` succeeds for a clean install. On upgrade, `config` updates the
  ; existing admin-protected service entry without a delete/recreate race.
  nsExec::ExecToStack '"$SYSDIR\sc.exe" create "LyricRailVolumeBroker" binPath= "\"$INSTDIR\lyricrail-volume-broker.exe\"" type= own start= auto error= normal obj= LocalSystem DisplayName= "LyricRail Volume Broker"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    nsExec::ExecToStack '"$SYSDIR\sc.exe" config "LyricRailVolumeBroker" binPath= "\"$INSTDIR\lyricrail-volume-broker.exe\"" type= own start= auto error= normal obj= LocalSystem DisplayName= "LyricRail Volume Broker"'
    !insertmacro LR_BROKER_REQUIRE_SUCCESS "configuration"
  ${EndIf}

  nsExec::ExecToStack '"$SYSDIR\sc.exe" description "LyricRailVolumeBroker" "Read-only, authenticated BitLocker status broker for LyricRail Studio."'
  !insertmacro LR_BROKER_REQUIRE_SUCCESS "description"
  nsExec::ExecToStack '"$SYSDIR\sc.exe" sidtype "LyricRailVolumeBroker" unrestricted'
  !insertmacro LR_BROKER_REQUIRE_SUCCESS "service-SID configuration"
  nsExec::ExecToStack '"$SYSDIR\sc.exe" failure "LyricRailVolumeBroker" reset= 86400 actions= restart/5000/restart/15000/none/0'
  !insertmacro LR_BROKER_REQUIRE_SUCCESS "failure-policy configuration"
  nsExec::ExecToStack '"$SYSDIR\sc.exe" failureflag "LyricRailVolumeBroker" 1'
  !insertmacro LR_BROKER_REQUIRE_SUCCESS "failure-policy activation"
  nsExec::ExecToStack '"$SYSDIR\sc.exe" start "LyricRailVolumeBroker"'
  !insertmacro LR_BROKER_REQUIRE_SUCCESS "startup"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToStack '"$SYSDIR\sc.exe" stop "LyricRailVolumeBroker"'
  Pop $0
  Pop $1
  Sleep 1000
  nsExec::ExecToStack '"$SYSDIR\sc.exe" delete "LyricRailVolumeBroker"'
  Pop $0
  Pop $1
  Delete "$INSTDIR\lyricrail-volume-broker.exe"
!macroend
