### screenly-ose ui ###

API = (window.Screenly ||= {}) # exports

date_settings_12hour =
  full_date: 'MM/DD/YYYY hh:mm:ss A',
  date: 'MM/DD/YYYY',
  time: 'hh:mm A',
  show_meridian: true,
  date_picker_format: 'mm/dd/yyyy'

date_settings_24hour =
  full_date: 'YYYY/MM/DD HH:mm:ss',
  date: 'YYYY/MM/DD',
  time: 'HH:mm',
  show_meridian: false,
  datepicker_format: 'yyyy/mm/dd'

date_settings = if use_24_hour_clock then date_settings_24hour else date_settings_12hour


API.date_to = date_to = (d) ->
  dd = moment (new Date d)
  string: -> dd.format date_settings.full_date
  date: -> dd.format date_settings.date
  time: -> dd.format date_settings.time

now = -> new Date()

get_template = (name) -> _.template ($ "##{name}-template").html()
delay = (wait, fn) -> _.delay fn, wait

mimetypes = [ [('jpg jpeg png pnm gif bmp'.split ' '), 'image']
              [('avi mkv mov mpg mpeg mp4 ts flv'.split ' '), 'video']]
viduris   = ('rtsp rtmp'.split ' ')


get_mimetype = (filename) =>
  scheme = (_.first filename.split ':').toLowerCase()
  match = scheme in viduris
  if match then return 'video'
  ext = (_.last filename.split '.').toLowerCase()
  mt = _.find mimetypes, (mt) -> ext in mt[0]
  if mt then mt[1] else null

url_test = (v) -> /(http|https|rtsp|rtmp):\/\/[\w-]+(\.?[\w-]+)+([\w.,@?^=%&amp;:\/~+#-]*[\w@?^=%&amp;\/~+#-])?/.test v
get_filename = (v) -> (v.replace /[\/\\\s]+$/g, '').replace /^.*[\\\/]/g, ''
insertWbr = (v) -> (v.replace /\//g, '/<wbr>').replace /\&/g, '&amp;<wbr>'

# Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = on

# Models
API.Asset = class Asset extends Backbone.Model
  idAttribute: "asset_id"
  fields: 'name mimetype uri start_date end_date duration'.split ' '
  defaults: =>
    name: ''
    mimetype: 'webpage'
    uri: ''
    is_active: false
    start_date: now()
    end_date: (moment().add 'days', 7).toDate()
    duration: default_duration
    is_enabled: 0
    nocache: 0
    play_order: 0
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


API.Assets = class Assets extends Backbone.Collection
  url: "/api/assets"
  model: Asset
  comparator: 'play_order'


# Views
API.View = {};
API.View.EditAssetView = class EditAssetView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']" # get field element
  $fv: (field, val...) => (@$f field).val val... # get or set filed value

  initialize: (options) =>
    @edit = options.edit
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
    _.delay (=> (@$f 'uri').focus()), 300
    no

  render: () =>
    @undelegateEvents()
    if @edit
      (@$ f).attr 'disabled', on for f in 'mimetype uri file_upload'.split ' '
      (@$ '#modalLabel').text "Edit Asset"
      (@$ '.asset-location').hide(); (@$ '.asset-location.edit').show()

    (@$ '.duration').toggle (true)
    @clickTabNavUri() if (@model.get 'mimetype') == 'webpage'

    for field in @model.fields
      if (@$fv field) != @model.get field
        @$fv field, @model.get field
    (@$ '.uri-text').html insertWbr @model.get 'uri'

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
    'submit form': 'save'
    'click .cancel': 'cancel'
    'change': 'change'
    'keyup': 'change'
    'click .tabnav-uri': 'clickTabNavUri'
    'click .tabnav-file_upload': 'clickTabNavUpload'
    'click .tabnav-file_upload, .tabnav-uri': 'displayAdvanced'
    'click .advanced-toggle': 'toggleAdvanced'
    'paste [name=uri]': 'updateUriMimetype'
    'change [name=file_upload]': 'updateFileUploadMimetype'
    'change [name=mimetype]': 'change_mimetype'

  save: (e) =>
    e.preventDefault()
    @viewmodel()
    save = null
    @model.set 'nocache', if (@$ 'input[name="nocache"]').prop 'checked' then 1 else 0
    if (@$ '#tab-file_upload').hasClass 'active'
      if not @$fv 'name'
        @$fv 'name', get_filename @$fv 'file_upload'
      (@$ '.progress').show()
      @$el.fileupload
        url: @model.url()
        progressall: (e, data) => if data.loaded and data.total
          (@$ '.progress .bar').css 'width', "#{data.loaded/data.total*100}%"
      save = @$el.fileupload 'send', fileInput: (@$f 'file_upload')
    else
      if not @model.get 'name'
        if get_mimetype @model.get 'uri'
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
      @model.collection.add @model unless @edit
    save.fail =>
      (@$ '.progress').hide()
      (@$ 'input, select').prop 'disabled', off
    no

  change: (e) =>
    @_change  ||= _.throttle (=>
      @viewmodel()
      @model.trigger 'change'
      @validate()
      yes), 500
    @_change arguments...

  change_mimetype: =>
    if (@$fv 'mimetype') != "video"
      (@$ '.zerohint').hide()
      @$fv 'duration', default_duration
    else
      (@$ '.zerohint').show()
      @$fv 'duration', 0

  validate: (e) =>
    that = this
    validators =
      duration: (v) =>
        if ('video' isnt @model.get 'mimetype') and (not (_.isNumber v*1 ) or v*1 < 1)
          'please enter a valid number'
      uri: (v) =>
        if @model.isNew() and ((that.$ '#tab-uri').hasClass 'active') and not url_test v
          'please enter a valid URL'
      file_upload: (v) =>
        if @model.isNew() and not v and not (that.$ '#tab-uri').hasClass 'active'
          return 'please select a file'
      end_date: (v) =>
        unless (new Date @$fv 'start_date') < (new Date @$fv 'end_date')
          'end date should be after start date'
    errors = ([field, v] for field, fn of validators when v = fn (@$fv field))

    (@$ ".control-group.warning .help-inline.warning").remove()
    (@$ ".control-group").removeClass 'warning'
    (@$ '[type=submit]').prop 'disabled', no
    for [field, v] in errors
      (@$ '[type=submit]').prop 'disabled', yes
      (@$ ".control-group.#{field}").addClass 'warning'
      (@$ ".control-group.#{field} .controls").append \
        $ ("<span class='help-inline warning'>#{v}</span>")


  cancel: (e) =>
    @model.rollback()
    unless @edit then @model.destroy()
    (@$el.children ":first").modal 'hide'

  clickTabNavUri: (e) => # TODO: clean
    if not (@$ '#tab-uri').hasClass 'active'
      (@$ 'ul.nav-tabs li').removeClass 'active'
      (@$ '.tab-pane').removeClass 'active'
      (@$ '.tabnav-uri').addClass 'active'
      (@$ '#tab-uri').addClass 'active'
      (@$f 'uri').focus()
      @updateUriMimetype()
    no

  clickTabNavUpload: (e) => # TODO: clean
    if not (@$ '#tab-file_upload').hasClass 'active'
      (@$ 'ul.nav-tabs li').removeClass 'active'
      (@$ '.tab-pane').removeClass 'active'
      (@$ '.tabnav-file_upload').addClass 'active'
      (@$ '#tab-file_upload').addClass 'active'
      (@$fv 'mimetype', 'image') if (@$fv 'mimetype') == 'webpage'
      @updateFileUploadMimetype
    no

  updateUriMimetype: => _.defer => @updateMimetype @$fv 'uri'
  updateFileUploadMimetype: => _.defer => @updateMimetype @$fv 'file_upload'
  updateMimetype: (filename) =>
    # also updates the filename label in the dom
    mt = get_mimetype filename
    (@$ '#file_upload_label').text (get_filename filename)
    @$fv 'mimetype', mt if mt
    @change_mimetype()

  toggleAdvanced: =>
    (@$ '.icon-play').toggleClass 'rotated'
    (@$ '.icon-play').toggleClass 'unrotated'
    (@$ '.collapse-advanced').collapse 'toggle'

  displayAdvanced: =>
    img = 'image' is @$fv 'mimetype'
    on_uri_tab = not @edit and (@$ '#tab-uri').hasClass 'active'
    edit = @edit and url_test @model.get 'uri'
    has_nocache = img and (on_uri_tab or edit)
    (@$ '.advanced-accordion').toggle has_nocache is on


API.View.AssetRowView = class AssetRowView extends Backbone.View
  tagName: "tr"

  initialize: (options) =>
    @template = get_template 'asset-row'

  render: =>
    @$el.html @template _.extend json = @model.toJSON(),
      name: insertWbr json.name # word break urls at slashes
      start_date: (date_to json.start_date).string()
      end_date: (date_to json.end_date).string()
    @$el.prop 'id', @model.get 'asset_id'
    (@$ ".delete-asset-button").popover content: get_template 'confirm-delete'
    (@$ ".toggle input").prop "checked", @model.get 'is_enabled'
    (@$ ".asset-icon").addClass switch @model.get "mimetype"
      when "video"   then "icon-facetime-video"
      when "image"   then "icon-picture"
      when "webpage" then "icon-globe"
      else ""
    @el

  events:
    'change .is_enabled-toggle input': 'toggleIsEnabled'
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

  edit: (e) =>
    new EditAssetView model: @model, edit:on
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
    @collection.get(id).set('play_order', i) for id, i in (@$ '#active-assets').sortable 'toArray'
    @collection.sort()

    $.post '/api/assets/order', ids: ((@$ '#active-assets').sortable 'toArray').join ','

  render: =>
    (@$ "##{which}-assets").html '' for which in ['active', 'inactive']

    @collection.each (model) =>
      which = if model.active() then 'active' else 'inactive'
      (@$ "##{which}-assets").append (new AssetRowView model: model).render()

    for which in ['inactive', 'active']
      @$(".#{which}-table thead").toggle !!(@$("##{which}-assets tr").length)
    if @$('#active-assets tr').length > 1
      @sorted.sortable 'enable'
      @update_order()
    else
      @sorted.sortable 'disable'
    @el


API.App = class App extends Backbone.View
  initialize: =>
    ($ window).ajaxError (e,r) =>
      ($ '#request-error').html (get_template 'request-error')()
      if (j = $.parseJSON r.responseText) and (err = j.error)
        ($ '#request-error .msg').text 'Server Error: ' + err
    ($ window).ajaxSuccess (data) =>
      ($ '#request-error').html ''

    (API.assets = new Assets()).fetch()
    API.assetsView = new AssetsView
      collection: API.assets
      el: @$ '#assets'


  events: {'click #add-asset-button': 'add'}

  add: (e) =>
    new EditAssetView model:
      new Asset {}, {collection: API.assets}
    no

