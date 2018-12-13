$().ready ->

  $('#request-error .close').click (e) ->
    $('#request-error .alert').hide()

  $('#btn-backup').click (e) ->
    btn_text = $('#btn-backup').text()
    $('#btn-backup').text "Preparing archive..."

    $('#btn-upload').prop 'disabled', yes
    $('#btn-backup').prop 'disabled', yes

    $.ajax({
      method: "POST"
      url: "api/v1/backup"
      timeout: 1800 * 1000
    })

    .done  (data, e) ->
      if (data)
        window.location = "static_with_mime/" + data + "?mime=application/x-tgz"

    .fail  (data, e) ->
      $('#request-error .alert').addClass 'alert-danger'
      $('#request-error .alert').removeClass 'alert-success'
      $('#request-error .alert').show()
      if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
        ($ '#request-error .msg').text 'Server Error: ' + err
      else
        ($ '#request-error .msg').text 'The operation failed. Please reload the page and try again.'

    .always (data, e) ->
      $('#btn-backup').text btn_text
      $('#btn-upload').prop 'disabled', no
      $('#btn-backup').prop 'disabled', no


  $('#btn-upload').click (e) ->
    e.preventDefault()
    $('[name="backup_upload"]').click()

  $('[name="backup_upload"]').fileupload
    url: "api/v1/recover"
    progressall: (e, data) -> if data.loaded and data.total
      valuenow = data.loaded/data.total*100
      $('.progress .bar').css 'width', valuenow + '%'
      $('.progress .bar').text 'Uploading: ' + Math.floor(valuenow) + '%'
    add: (e, data) ->
      $('#btn-upload').hide()
      $('#btn-backup').hide()
      $('.progress').show()

      data.submit()
    done: (e, data) ->
      if (data.jqXHR.responseText != "") and (message = $.parseJSON data.jqXHR.responseText)
        $('#request-error .alert').show()
        $('#request-error .alert').addClass 'alert-success'
        $('#request-error .alert').removeClass 'alert-danger'
        ($ '#request-error .msg').text message
    fail: (e, data) ->
      $('#request-error .alert').show()
      $('#request-error .alert').addClass 'alert-danger'
      $('#request-error .alert').removeClass 'alert-success'
      if (data.jqXHR.responseText != "") and (j = $.parseJSON data.jqXHR.responseText) and (err = j.error)
        ($ '#request-error .msg').text 'Server Error: ' + err
      else
        ($ '#request-error .msg').text 'The operation failed. Please reload the page and try again.'
    always: (e, data) ->
      $('.progress').hide()
      $('#btn-upload').show()
      $('#btn-backup').show()

  $('#btn-reset').click (e) ->
    $.get '/api/v1/reset_wifi'
    .done  (e) ->
      $('#request-error .alert').show()
      $('#request-error .alert').addClass 'alert-success'
      $('#request-error .alert').removeClass 'alert-danger'
      ($ '#request-error .msg').text 'Reset was successful. Please reboot the device.'

  $('#auth_checkbox p span').click (e) ->
    if $('input:checkbox[name="use_auth"]').is(':checked')
      $('#user_group, #password_group, #password2_group').hide()
      $('input:text[name="user"]').val('')
      $('input:password[name="password"]').val('')
      $('input:password[name="password2"]').val('')
    else
      $('#user_group, #password_group, #password2_group, #curpassword_group').show()

  if $('input:checkbox[name="use_auth"]').is(':checked')
    $('#user_group, #password_group, #password2_group, #curpassword_group').show()
  else
    $('#user_group, #password_group, #password2_group, #curpassword_group').hide()

  $('#btn-upgrade').click (e) ->
    $('#upgrade-modal').modal 'show'
  $('#close-upgrade-btn').click (e) ->
    $('#upgrade-modal').modal 'hide'
  $('#start-upgrade-btn').click (e) ->
    $('#start-upgrade-btn').prop 'disabled', yes
    $.post "api/v1/upgrade_screenly" ,
      branch: $('#branch-group-radio input:radio:checked').val()
      manage_network: $('input:checkbox[name="manage_network"]').is(':checked')
      system_upgrade: $('input:checkbox[name="system_upgrade"]').is(':checked')
    .done  (data, e) ->
      get_status = (id) ->
        $.get "/upgrade_status/" + id
        .done  (data, e, jqXHR) ->
          if data.status
            ($ '#upgrade_logs').text data.status
            ($ '#upgrade_logs').scrollTop(($ '#upgrade_logs').prop 'scrollHeight')
          if jqXHR.status == 202
            setTimeout ->
              get_status(id)
            ,1000
          else
            ($ '#upgrade_logs').append '\nScreenly-OSE update was finished'
            ($ '#upgrade_logs').scrollTop(($ '#upgrade_logs').prop 'scrollHeight')
            window.onbeforeunload = null
            $('#start-upgrade-btn').prop 'disabled', no
        .fail  (data, e) ->
          if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
            ($ '#upgrade_logs').append 'Server Error: ' + err
          else
            ($ '#upgrade_logs').append 'The operation failed. Please reload the page and try again.'

      ($ '#upgrade_logs').text 'Screenly-OSE upgrade has started successfully.'
      window.onbeforeunload = ->
        no
      get_status(data.id)
    .fail  (data, e) ->
      if (data.responseText != "") and (j = $.parseJSON data.responseText) and (err = j.error)
        ($ '#upgrade_logs').append 'Server Error: ' + err
      else
        ($ '#upgrade_logs').append 'The operation failed. Please reload the page and try again.'
      $('#start-upgrade-btn').prop 'disabled', no
