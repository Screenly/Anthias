import '../sass/anthias.scss'

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
      url: "/api/v2/backup"
      timeout: 1800 * 1000
    })

    .done  (data, e) ->
      if (data)
        window.location = "/static_with_mime/" + data + "?mime=application/x-tgz"

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
    url: "/api/v2/recover"
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

  $("#btn-reboot-system").click (e) ->
    if confirm "Are you sure you want to reboot your device?"
      $.post "/api/v2/reboot"
      .done  (e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-success"
        ($ "#request-error .alert").removeClass "alert-danger"
        ($ "#request-error .msg").text "Reboot has started successfully."
      .fail (data, e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-danger"
        ($ "#request-error .alert").removeClass "alert-success"
        if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
          ($ "#request-error .msg").text "Server Error: " + err
        else
          ($ "#request-error .msg").text "The operation failed. Please reload the page and try again."

  $("#btn-shutdown-system").click (e) ->
    if confirm "Are you sure you want to shutdown your device?"
      $.post "/api/v2/shutdown"
      .done  (e) ->
        ($ "#request-error .alert").show()
        ($ "#request-error .alert").addClass "alert-success"
        ($ "#request-error .alert").removeClass "alert-danger"
        ($ "#request-error .msg").text """
          Device shutdown has started successfully.
          Soon you will be able to unplug the power from your Raspberry Pi.
        """
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
