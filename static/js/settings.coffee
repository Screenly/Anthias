$().ready ->

  $("#request-error .close").click (e) ->
    $("#request-error .alert").hide()

  $("#btn-backup").click (e) ->
    btnText = $("#btn-backup").text()
    $("#btn-backup").text "Preparing archive..."

    $("#btn-upload").prop "disabled", yes
    $("#btn-backup").prop "disabled", yes

    $.ajax({
      method: "POST"
      url: "api/v1/backup"
      timeout: 1800 * 1000
    })

    .done  (data, e) ->
      if (data)
        window.location = "static_with_mime/" + data + "?mime=application/x-tgz"

    .fail  (data, e) ->
      $("#request-error .alert").addClass "alert-danger"
      $("#request-error .alert").removeClass "alert-success"
      $("#request-error .alert").show()
      if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
        ($ "#request-error .msg").text "Server Error: " + err
      else
        ($ "#request-error .msg").text "The operation failed. Please reload the page and try again."

    .always (data, e) ->
      $("#btn-backup").text btnText
      $("#btn-upload").prop "disabled", no
      $("#btn-backup").prop "disabled", no


  $("#btn-upload").click (e) ->
    e.preventDefault()
    $("[name='backup_upload']").click()

  $("[name='backup_upload']").fileupload
    url: "api/v1/recover"
    progressall: (e, data) -> if data.loaded and data.total
      valuenow = data.loaded/data.total*100
      $(".progress .bar").css "width", valuenow + "%"
      $(".progress .bar").text "Uploading: " + Math.floor(valuenow) + "%"
    add: (e, data) ->
      $("#btn-upload").hide()
      $("#btn-backup").hide()
      $(".progress").show()

      data.submit()
    done: (e, data) ->
      if (data.jqXHR.responseText != "") and (message = $.parseJSON data.jqXHR.responseText)
        $("#request-error .alert").show()
        $("#request-error .alert").addClass "alert-success"
        $("#request-error .alert").removeClass "alert-danger"
        ($ "#request-error .msg").text message
    fail: (e, data) ->
      $("#request-error .alert").show()
      $("#request-error .alert").addClass "alert-danger"
      $("#request-error .alert").removeClass "alert-success"
      if (data.jqXHR.responseText != "") and (j = $.parseJSON data.jqXHR.responseText) and (err = j.error)
        ($ "#request-error .msg").text "Server Error: " + err
      else
        ($ "#request-error .msg").text "The operation failed. Please reload the page and try again."
    always: (e, data) ->
      $(".progress").hide()
      $("#btn-upload").show()
      $("#btn-backup").show()

  $("#btn-reset").click (e) ->
    if confirm "Are you sure you want to reset your wifi configuration?"
      $.get "/api/v1/reset_wifi"
      .done  (e) ->
        $("#request-error .alert").show()
        $("#request-error .alert").addClass "alert-success"
        $("#request-error .alert").removeClass "alert-danger"
        ($ "#request-error .msg").text "Reset was successful. Please reboot the device."
      .error (e) ->
        document.location.reload()

  start_date = new Date()
  start_date_usb_file = $("#view-usb-assets-file-modal [name='start_date_date']")
  start_date_usb_file.datepicker autoclose: yes, format: 'mm/dd/yyyy'
  start_date_usb_file.datepicker 'setDate', start_date

  end_date = new Date(new Date().setDate(start_date.getDate() + 7))
  end_date_usb_file = $("#view-usb-assets-file-modal [name='end_date_date']")
  end_date_usb_file.datepicker autoclose: yes, format: 'mm/dd/yyyy'
  end_date_usb_file.datepicker 'setDate', end_date

  $("#btn-view-usb-assets-file").click (e) ->
    $("#view-usb-assets-file-modal").modal "show"

  $("#close-view-usb-assets-file-btn").click (e) ->
    $("#view-usb-assets-file-modal").modal "hide"

  $("#generate-usb-assets-key-btn").click (e) ->
    $.get("/api/v1/generate_usb_assets_key")
    .done (data, e) ->
      if (data)
        $("#usb-assets-key-badge").val data

  $("#btn-download-usb-assets-key").click (e) ->
    filename = "usb_assets_key.yaml"

    text = "screenly:\r\n"
    text += "  key: \"#{($("#usb-assets-key-badge")).val().trim()}\"\r\n"
    text += "  activate: #{Boolean($("input[name=\"activate_assets\"]").prop "checked")}\r\n"
    text += "  copy: #{Boolean($("input[name=\"copy_assets\"]").prop "checked")}\r\n"
    text += "  start_date: \"#{start_date_usb_file.val()}\"\r\n"
    text += "  end_date: \"#{end_date_usb_file.val()}\"\r\n"
    text += "  duration: #{$("input[name=\"duration\"]").val()}"

    blob = new Blob([text], {type: 'text/csv'})
    if (window.navigator.msSaveOrOpenBlob)
      window.navigator.msSaveBlob(blob, filename)
    else
      elem = window.document.createElement('a');
      elem.href = window.URL.createObjectURL(blob);
      elem.download = filename;
      document.body.appendChild(elem);
      elem.click();
      document.body.removeChild(elem);

  $("#btn-upgrade").click (e) ->
    $("#upgrade-modal").modal "show"

  $("#close-upgrade-btn").click (e) ->
    $("#upgrade-modal").modal "hide"

  $("#start-upgrade-btn").click (e) ->
    $("#start-upgrade-btn").prop "disabled", yes
    ($ "#upgrade_logs").text ""
    $.post "api/v1/upgrade_screenly" ,
      "branch": $("#branch-group-radio input:radio:checked").val()
      "manage_network": $("input:checkbox[name='manage_network']").is(":checked")
      "system_upgrade": $("input:checkbox[name='system_upgrade']").is(":checked")
    .done  (data, e) ->
      getStatus = (id) ->
        $.get "/upgrade_status/" + id
        .done  (data, e, jqXHR) ->
          if data.status
            scrollToBottom = ($ "#upgrade_logs").scrollTop() + ($ "#upgrade_logs").outerHeight() == ($ "#upgrade_logs").prop "scrollHeight"
            ($ "#upgrade_logs").text data.status
            if scrollToBottom then ($ "#upgrade_logs").scrollTop(($ "#upgrade_logs").prop "scrollHeight")
          if jqXHR.status == 202
            setTimeout ->
              getStatus(id)
            ,1000
          else
            ($ "#upgrade_logs").append "\nScreenly-OSE update was finished"
            ($ "#upgrade_logs").scrollTop(($ "#upgrade_logs").prop "scrollHeight")
            window.onbeforeunload = null
            $("#start-upgrade-btn").prop "disabled", no
        .fail  (data, e) ->
          if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
            ($ "#upgrade_logs").append "Server Error: " + err
          else
            ($ "#upgrade_logs").append "The operation failed. Please reload the page and try again."

      ($ "#upgrade_logs").text "Screenly-OSE upgrade has started successfully."
      window.onbeforeunload = ->
        no
      getStatus(data.id)
    .fail  (data, e) ->
      if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
        ($ "#upgrade_logs").append "Server Error: " + err
      else
        ($ "#upgrade_logs").append "The operation failed. Please reload the page and try again."
      $("#start-upgrade-btn").prop "disabled", no

  $("#btn-reboot-system").click (e) ->
    if confirm "Are you sure you want to reboot your Screenly?"
      $.post "/api/v1/reboot_screenly"
      .done  (e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-success"
        ($ "#request-error .alert").removeClass "alert-danger"
        ($ "#request-error .msg").text "Screenly reboot has started successfully."
      .fail (data, e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-danger"
        ($ "#request-error .alert").removeClass "alert-success"
        if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
          ($ "#request-error .msg").text "Server Error: " + err
        else
          ($ "#request-error .msg").text "The operation failed. Please reload the page and try again."

  $("#btn-shutdown-system").click (e) ->
    if confirm "Are you sure you want to shutdown your Screenly?"
      $.post "/api/v1/shutdown_screenly"
      .done  (e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-success"
        ($ "#request-error .alert").removeClass "alert-danger"
        ($ "#request-error .msg").text "Screenly shutdown has started successfully. Soon you will be able to unplug the power from your Raspberry Pi."
      .fail (data, e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-danger"
        ($ "#request-error .alert").removeClass "alert-success"
        if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
          ($ "#request-error .msg").text "Server Error: " + err
        else
          ($ "#request-error .msg").text "The operation failed. Please reload the page and try again."

  toggle_chunk = () ->
    $("[id^=auth_chunk]").hide()
    $.each $('#auth_backend option'), (e, t) ->
      $('#auth_backend-'+t.value).toggle $('#auth_backend').val() == t.value

  $('#auth_backend').change (e) ->
    toggle_chunk()

  toggle_chunk()
