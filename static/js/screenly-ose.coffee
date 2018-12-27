### screenly-ose ui ###

$().ready ->
  $('#subsribe-form-container').popover content: get_template 'subscribe-form'


API = (window.Screenly ||= {}) # exports

date_settings_12hour =
  full_date: 'MM/DD/YYYY hh:mm:ss A',
  date: 'MM/DD/YYYY',
  time: 'hh:mm A',
  show_meridian: true,
  date_picker_format: 'mm/dd/yyyy'

date_settings_24hour =
  full_date: 'MM/DD/YYYY HH:mm:ss',
  date: 'MM/DD/YYYY',
  time: 'HH:mm',
  show_meridian: false,
  datepicker_format: 'mm/dd/yyyy'

date_settings = if use_24_hour_clock then date_settings_24hour else date_settings_12hour


API.date_to = date_to = (d) ->
  # Cross-browser UTC to localtime conversion
  dd = moment.utc(d).local()
  string: -> dd.format date_settings.full_date
  date: -> dd.format date_settings.date
  time: -> dd.format date_settings.time

now = -> new Date()

get_template = (name) -> _.template ($ "##{name}-template").html()
delay = (wait, fn) -> _.delay fn, wait

mimetypes = [ [('jpg jpeg png pnm gif bmp'.split ' '), 'image']
              [('avi mkv mov mpg mpeg mp4 ts flv'.split ' '), 'video']]
viduris   = ('rtsp rtmp'.split ' ')
domains = [ [('www.youtube.com youtu.be'.split ' '), 'youtube_asset']]


get_mimetype = (filename) =>
  scheme = (_.first filename.split ':').toLowerCase()
  match = scheme in viduris
  if match then return 'streaming'

  domain = (_.first ((_.last filename.split '//').toLowerCase()).split '/')
  mt = _.find domains, (mt) -> domain in mt[0]
  if mt and domain in mt[0] then return mt[1]

  ext = (_.last filename.split '.').toLowerCase()
  mt = _.find mimetypes, (mt) -> ext in mt[0]
  if mt then mt[1] else null

duration_seconds_to_human_readable = (secs) =>
  duration_string = ''
  sec_int = parseInt(secs)

  if ((hours = Math.floor(sec_int / 3600)) > 0)
    duration_string += hours + ' hours '
  if ((minutes = Math.floor(sec_int / 60) % 60) > 0)
    duration_string += minutes + ' min '
  if ((seconds = (sec_int % 60)) > 0)
    duration_string += seconds + ' sec'

  return duration_string

url_test = (v) -> /(http|https|rtsp|rtmp):\/\/[\w-]+(\.?[\w-]+)+([\w.,@?^=%&amp;:\/~+#-]*[\w@?^=%&amp;\/~+#-])?/.test v
get_filename = (v) -> (v.replace /[\/\\\s]+$/g, '').replace /^.*[\\\/]/g, ''
truncate_str = (v) -> v.replace /(.{100})..+/, "$1..."
insertWbr = (v) -> (v.replace /\//g, '/<wbr>').replace /\&/g, '&amp;<wbr>'

# Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = off

# Models
API.Asset = class Asset extends Backbone.Model
  idAttribute: "asset_id"
  fields: 'name mimetype uri start_date end_date duration skip_asset_check'.split ' '
  defaults: =>
    name: ''
    mimetype: 'webpage'
    uri: ''
    is_active: 1
    start_date: ''
    end_date: ''
    duration: default_duration
    is_enabled: 0
    is_processing: 0
    nocache: 0
    play_order: 0
    skip_asset_check: 0
  active: =>
    if @get('is_enabled') and @get('start_date') and @get('end_date')
      at = now()
      start_date = new Date(@get('start_date'));
      end_date = new Date(@get('end_date'));
      return start_date <= at <= end_date
    else
      return false

  backup: =>
    @backup_attributes = @toJSON()

  rollback: =>
    if @backup_attributes
      @set @backup_attributes
      @backup_attributes = undefined
  old_name: =>
    if @backup_attributes
      return @backup_attributes.name


API.Assets = class Assets extends Backbone.Collection
  url: "/api/v1.2/assets"
  model: Asset
  comparator: 'play_order'


# Views
API.View = {};

API.View.AddAssetView = class AddAssetView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']" # get field element
  $fv: (field, val...) => (@$f field).val val... # get or set filed value

  initialize: (oprions) =>
    ($ 'body').append @$el.html get_template 'asset-modal'
    (@$el.children ":first").modal()
    (@$ '.cancel').val 'Back to Assets'

    deadlines = start: now(), end: (moment().add 'days', 30).toDate()
    for own tag, deadline of deadlines
      d = date_to deadline
      @.$fv "#{tag}_date_date", d.date()
      @.$fv "#{tag}_date_time", d.time()

    no

  viewmodel:(model) =>
    for which in ['start', 'end']
      @$fv "#{which}_date", (new Date (@$fv "#{which}_date_date") + " " + (@$fv "#{which}_date_time")).toISOString()
    for field in model.fields when not (@$f field).prop 'disabled'
      model.set field, (@$fv field), silent:yes

  events:
    'change': 'change'
    'click #save-asset': 'save'
    'click .cancel': 'cancel'
    'hidden.bs.modal': 'destroyFileUploadWidget'
    'click .tabnav-uri': 'clickTabNavUri'
    'click .tabnav-file_upload': 'clickTabNavUpload'
    'change .is_enabled-skip_asset_check_checkbox': 'toggleSkipAssetCheck'

  save: (e) =>
    if ((@$fv 'uri') == '')
      return no
    if (@$ '#tab-uri').hasClass 'active'
      model =  new Asset {}, {collection: API.assets}
      @$fv 'mimetype', ''
      @updateUriMimetype()
      @viewmodel model
      model.set {name: model.get 'uri'}, silent:yes
      save = model.save()

      (@$ 'input').prop 'disabled', on
      save.done (data) =>
        model.id = data.asset_id
        (@$el.children ":first").modal 'hide'
        _.extend model.attributes, data
        model.collection.add model
      save.fail =>
        (@$ 'input').prop 'disabled', off
        model.destroy()
    no

  toggleSkipAssetCheck: (e) =>
    @$fv 'skip_asset_check', if parseInt((@$fv 'skip_asset_check')) == 1 then 0 else 1

  change_mimetype: =>
    if (@$fv 'mimetype') == "video"
      @$fv 'duration', 0
    else if (@$fv 'mimetype') == "streaming"
      @$fv 'duration', default_streaming_duration
    else
      @$fv 'duration', default_duration

  clickTabNavUpload: (e) =>
    if not (@$ '#tab-file_upload').hasClass 'active'
      (@$ 'ul.nav-tabs li').removeClass 'active show'
      (@$ '.tab-pane').removeClass 'active'
      (@$ '.tabnav-file_upload').addClass 'active show'
      (@$ '#tab-file_upload').addClass 'active'
      (@$ '.uri').hide()
      (@$ '.skip_asset_check_checkbox').hide()
      (@$ '#save-asset').hide()
      that = this
      (@$ "[name='file_upload']").fileupload
        autoUpload: false
        sequentialUploads: true
        maxChunkSize: 5000000 #5 MB
        url: 'api/v1/file_asset'
        progressall: (e, data) => if data.loaded and data.total
          (@$ '.progress .bar').css 'width', "#{data.loaded / data.total * 100}%"
        add: (e, data) ->
          (that.$ '.status').hide()
          (that.$ '.progress').show()

          model =  new Asset {}, {collection: API.assets}
          filename = data['files'][0]['name']
          that.$fv 'name', filename
          that.updateFileUploadMimetype(filename)
          that.viewmodel model

          data.submit()
          .success (uri) =>
            model.set {uri: uri}, silent:yes

            save = model.save()
            save.done (data) =>
              model.id = data.asset_id
              _.extend model.attributes, data
              model.collection.add model
            save.fail =>
              model.destroy()
          .error =>
            model.destroy()
        stop: (e) ->
          (that.$ '.progress').hide()
          (that.$ '.progress .bar').css 'width', "0"
        done: (e, data) ->
          (that.$ '.status').show()
          (that.$ '.status').html 'Upload completed.'
          setTimeout ->
            (that.$ '.status').fadeOut('slow')
          , 5000
    no

  clickTabNavUri: (e) => # TODO: clean
    if not (@$ '#tab-uri').hasClass 'active'
      (@$ "[name='file_upload']").fileupload 'destroy'
      (@$ 'ul.nav-tabs li').removeClass 'active show'
      (@$ '.tab-pane').removeClass 'active'
      (@$ '.tabnav-uri').addClass 'active show'
      (@$ '#tab-uri').addClass 'active'
      (@$ '#save-asset').show()
      (@$ '.uri').show()
      (@$ '.skip_asset_check_checkbox').show()
      (@$ '.status').hide()
      (@$f 'uri').focus()

  updateUriMimetype: => @updateMimetype @$fv 'uri'
  updateFileUploadMimetype: (filename) => @updateMimetype filename
  updateMimetype: (filename) =>
    mt = get_mimetype filename
    @$fv 'mimetype', if mt then mt else new Asset().defaults()['mimetype']
    @change_mimetype()

  change: (e) =>
    @_change  ||= _.throttle (=>
      @validate()
      yes), 500
    @_change arguments...

  validate: (e) =>
    that = this
    validators =
      uri: (v) =>
        if v
          if ((that.$ '#tab-uri').hasClass 'active') and not url_test v
            'please enter a valid URL'
    errors = ([field, v] for field, fn of validators when v = fn (@$fv field))

    (@$ ".form-group .help-inline.invalid-feedback").remove()
    (@$ ".form-group .form-control").removeClass 'is-invalid'
    (@$ '[type=submit]').prop 'disabled', no
    for [field, v] in errors
      (@$ '[type=submit]').prop 'disabled', yes
      (@$ ".form-group.#{field} .form-control").addClass 'is-invalid'
      (@$ ".form-group.#{field} .controls").append \
        $ ("<span class='help-inline invalid-feedback'>#{v}</span>")

  cancel: (e) =>
    (@$el.children ":first").modal 'hide'

  destroyFileUploadWidget: (e) =>
    if (@$ '#tab-file_upload').hasClass 'active'
      (@$ "[name='file_upload']").fileupload 'destroy'


API.View.EditAssetView = class EditAssetView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']" # get field element
  $fv: (field, val...) => (@$f field).val val... # get or set filed value

  initialize: (options) =>
    ($ 'body').append @$el.html get_template 'asset-modal'
    (@$ 'input.time').timepicker
      minuteStep: 5, showInputs: yes, disableFocus: yes, showMeridian: date_settings.show_meridian

    (@$ 'input[name="nocache"]').prop 'checked', @model.get 'nocache'
    (@$ '.modal-header .close').remove()
    (@$el.children ":first").modal()

    @model.backup()

    @model.bind 'change', @render

    @render()
    @validate()
    no

  render: () =>
    @undelegateEvents()
    (@$ f).attr 'disabled', on for f in 'mimetype uri file_upload'.split ' '
    (@$ '#modalLabel').text "Edit Asset"
    (@$ '.asset-location').hide(); (@$ '.uri').hide(); (@$ '.skip_asset_check_checkbox').hide()
    (@$ '.asset-location.edit').show()
    (@$ '.mime-select').prop('disabled', 'true')

    if (@model.get 'mimetype') == 'video'
      (@$f 'duration').prop 'disabled', on

    for field in @model.fields
      if (@$fv field) != @model.get field
        @$fv field, @model.get field
    (@$ '.uri-text').html insertWbr truncate_str (@model.get 'uri')

    for which in ['start', 'end']
      d = date_to @model.get "#{which}_date"
      @$fv "#{which}_date_date", d.date()
      (@$f "#{which}_date_date").datepicker autoclose: yes, format: date_settings.datepicker_format
      (@$f "#{which}_date_date").datepicker 'setValue', d.date()
      @$fv "#{which}_date_time", d.time()

    @displayAdvanced()
    @delegateEvents()
    no

  viewmodel: =>
    for which in ['start', 'end']
      @$fv "#{which}_date", (new Date (@$fv "#{which}_date_date") + " " + (@$fv "#{which}_date_time")).toISOString()
    for field in @model.fields when not (@$f field).prop 'disabled'
      @model.set field, (@$fv field), silent:yes

  events:
    'click #save-asset': 'save'
    'click .cancel': 'cancel'
    'change': 'change'
    'keyup': 'change'
    'click .advanced-toggle': 'toggleAdvanced'

  changeLoopTimes: =>
    current_date = new Date()
    end_date = new Date()

    switch @$('#loop_times').val()
      when "day"
        @setLoopDateTime (date_to current_date), (date_to end_date.setDate(current_date.getDate() + 1))
      when "week"
        @setLoopDateTime (date_to current_date), (date_to end_date.setDate(current_date.getDate() + 7))
      when "month"
        @setLoopDateTime (date_to current_date), (date_to end_date.setMonth(current_date.getMonth() + 1))
      when "year"
        @setLoopDateTime (date_to current_date), (date_to end_date.setFullYear(current_date.getFullYear() + 1))
      when "forever"
        @setLoopDateTime (date_to current_date), (date_to end_date.setFullYear(9999))
      when "manual"
        @setDisabledDatepicker(false)
        (@$ "#manul_date").show()
        return
      else
        return
    @setDisabledDatepicker(true)
    (@$ "#manul_date").hide()

  save: (e) =>
    @viewmodel()
    save = null
    @model.set 'nocache', if (@$ 'input[name="nocache"]').prop 'checked' then 1 else 0

    if not @model.get 'name'
      if @model.old_name()
        @model.set {name: @model.old_name()}, silent:yes
      else if get_mimetype @model.get 'uri'
        @model.set {name: get_filename @model.get 'uri'}, silent:yes
      else
        @model.set {name: @model.get 'uri'}, silent:yes
    save = @model.save()

    (@$ 'input, select').prop 'disabled', on
    save.done (data) =>
      @model.id = data.asset_id
      @collection.add @model if not @model.collection
      (@$el.children ":first").modal 'hide'
      _.extend @model.attributes, data
    save.fail =>
      (@$ '.progress').hide()
      (@$ 'input, select').prop 'disabled', off
    no

  change: (e) =>
    @_change  ||= _.throttle (=>
      @changeLoopTimes()
      @viewmodel()
      @model.trigger 'change'
      @validate()
      yes), 500
    @_change arguments...

  validate: (e) =>
    that = this
    validators =
      duration: (v) =>
        if ('video' isnt @model.get 'mimetype') and (not (_.isNumber v*1 ) or v*1 < 1)
          'please enter a valid number'
      end_date: (v) =>
        unless (new Date @$fv 'start_date') < (new Date @$fv 'end_date')
          'end date should be after start date'
    errors = ([field, v] for field, fn of validators when v = fn (@$fv field))

    (@$ ".form-group .help-inline.invalid-feedback").remove()
    (@$ ".form-group .form-control").removeClass 'is-invalid'
    (@$ '[type=submit]').prop 'disabled', no
    for [field, v] in errors
      (@$ '[type=submit]').prop 'disabled', yes
      (@$ ".form-group.#{field} .form-control").addClass 'is-invalid'
      (@$ ".form-group.#{field} .controls").append \
        $ ("<span class='help-inline invalid-feedback'>#{v}</span>")


  cancel: (e) =>
    @model.rollback()
    (@$el.children ":first").modal 'hide'

  toggleAdvanced: =>
    (@$ '.fa-play').toggleClass 'rotated'
    (@$ '.fa-play').toggleClass 'unrotated'
    (@$ '.collapse-advanced').collapse 'toggle'

  displayAdvanced: =>
    img = 'image' is @$fv 'mimetype'
    edit = url_test @model.get 'uri'
    has_nocache = img and edit
    (@$ '.advanced-accordion').toggle has_nocache is on

  setLoopDateTime: (start_date, end_date) =>
    @$fv "start_date_date", start_date.date()
    (@$f "start_date_date").datepicker autoclose: yes, format: date_settings.datepicker_format
    (@$f "start_date_date").datepicker 'setDate', new Date(start_date.date())
    @$fv "start_date_time", start_date.time()
    @$fv "end_date_date", end_date.date()
    (@$f "end_date_date").datepicker autoclose: yes, format: date_settings.datepicker_format
    (@$f "end_date_date").datepicker 'setDate', new Date(end_date.date())
    @$fv "end_date_time", end_date.time()

    (@$ ".form-group .help-inline.invalid-feedback").remove()
    (@$ ".form-group .form-control").removeClass 'is-invalid'
    (@$ '[type=submit]').prop 'disabled', no

  setDisabledDatepicker: (b) =>
    for which in ['start', 'end']
      (@$f "#{which}_date_date").attr  'disabled', b
      (@$f "#{which}_date_time").attr  'disabled', b

API.View.AssetRowView = class AssetRowView extends Backbone.View
  tagName: "tr"

  initialize: (options) =>
    @template = get_template 'asset-row'

  render: =>
    @$el.html @template _.extend json = @model.toJSON(),
      name: insertWbr truncate_str json.name # word break urls at slashes
      duration: duration_seconds_to_human_readable(json.duration)
      start_date: (date_to json.start_date).string()
      end_date: (date_to json.end_date).string()
    @$el.prop 'id', @model.get 'asset_id'
    (@$ ".delete-asset-button").popover content: get_template 'confirm-delete'
    (@$ ".toggle input").prop "checked", @model.get 'is_enabled'
    (@$ ".asset-icon").addClass switch @model.get "mimetype"
      when "video"     then "fas fa-video"
      when "streaming" then "fas fa-video"
      when "image"     then "far fa-image"
      when "webpage"   then "fas fa-globe-americas"
      else ""

    if (@model.get "is_processing") == 1
      (@$ 'input, button').prop 'disabled', on
      (@$ ".asset-toggle").html get_template 'processing-message'

    @el

  events:
    'change .is_enabled-toggle input': 'toggleIsEnabled'
    'click .download-asset-button': 'download'
    'click .edit-asset-button': 'edit'
    'click .delete-asset-button': 'showPopover'

  toggleIsEnabled: (e) =>
    val = (1 + @model.get 'is_enabled') % 2
    @model.set is_enabled: val
    @setEnabled off
    save = @model.save()
    save.done => @setEnabled on
    save.fail =>
      @model.set @model.previousAttributes(), silent:yes # revert changes
      @setEnabled on
      @render()
    yes

  setEnabled: (enabled) => if enabled
      @$el.removeClass 'warning'
      @delegateEvents()
      (@$ 'input, button').prop 'disabled', off
    else
      @hidePopover()
      @undelegateEvents()
      @$el.addClass 'warning'
      (@$ 'input, button').prop 'disabled', on

  download: (e) =>
    r = $.get '/api/v1/assets/' + @model.id + '/content'
        .success (result) ->
          switch result['type']
            when 'url'
              window.open(result['url'])
            when 'file'
              content = base64js.toByteArray(result['content'])

              mimetype = result['mimetype']
              fn = result['filename']

              blob = new Blob([content], {type: mimetype})
              url = URL.createObjectURL(blob)

              a = document.createElement('a')
              document.body.appendChild(a)
              a.download = fn
              a.href = url
              a.click()

              URL.revokeObjectURL(url)
              a.remove()
    no

  edit: (e) =>
    new EditAssetView model: @model
    no

  delete: (e) =>
    @hidePopover()
    if (xhr = @model.destroy()) is not false
      xhr.done => @remove()
    else
      @remove()
    no

  showPopover: =>
    if not ($ '.popover').length
      (@$ ".delete-asset-button").popover 'show'
      ($ '.confirm-delete').click @delete
      ($ window).one 'click', @hidePopover
    no

  hidePopover: =>
    (@$ ".delete-asset-button").popover 'hide'
    no


API.View.AssetsView = class AssetsView extends Backbone.View
  initialize: (options) =>
    @collection.bind event, @render for event in ('reset add remove sync'.split ' ')
    @sorted = (@$ '#active-assets').sortable
      containment: 'parent'
      axis: 'y'
      helper: 'clone'
      update: @update_order

  update_order: =>
    active = (@$ '#active-assets').sortable 'toArray'

    @collection.get(id).set('play_order', i) for id, i in active
    @collection.get(el.id).set('play_order', active.length) for el in (@$ '#inactive-assets tr').toArray()

    $.post '/api/v1/assets/order', ids: ((@$ '#active-assets').sortable 'toArray').join ','

  render: =>
    @collection.sort()

    (@$ "##{which}-assets").html '' for which in ['active', 'inactive']

    @collection.each (model) =>
      which = if model.active() then 'active' else 'inactive'
      (@$ "##{which}-assets").append (new AssetRowView model: model).render()

    for which in ['inactive', 'active']
      @$(".#{which}-table thead").toggle !!(@$("##{which}-assets tr").length)

    @update_order()

    @el


API.App = class App extends Backbone.View
  initialize: =>
    ($ window).ajaxError (e,r) =>
      ($ '#request-error').html (get_template 'request-error')()
      if (j = $.parseJSON r.responseText) and (err = j.error)
        ($ '#request-error .msg').text 'Server Error: ' + err
      ($ '#request-error').show()
      setTimeout ->
        ($ '#request-error').fadeOut('slow')
      , 5000
    ($ window).ajaxSuccess (event, request, settings) ->
      if (settings.url == new Assets().url) and (settings.type == 'POST')
        ($ '#request-error').html (get_template 'request-success')()
        ($ '#request-error .msg').text 'Asset has been successfully uploaded.'
        ($ '#request-error').show()
        setTimeout ->
          ($ '#request-error').fadeOut('slow')
        , 5000

    (API.assets = new Assets()).fetch()
    API.assetsView = new AssetsView
      collection: API.assets
      el: @$ '#assets'

    for address in ws_addresses
      try
        ws = new WebSocket address
        ws.onmessage = (x) ->
          model = API.assets.get(x.data)
          if model
            save = model.fetch()
      catch error
        no

  events:
    'click #add-asset-button': 'add',
    'click #previous-asset-button': 'previous',
    'click #next-asset-button': 'next'

  add: (e) =>
    new AddAssetView
    no

  previous: (e) =>
    $.get '/api/v1/assets/control/previous'

  next: (e) =>
    $.get '/api/v1/assets/control/next'
