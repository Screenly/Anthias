### screenly-ose ui ###

API = (window.Screenly ||= {}) # exports
API.date_to = date_to =
  iso:       (d) -> (new Date d).toISOString()
  string:    (d) -> (moment (new Date d)).format("MM/DD/YYYY hh:mm:ss A")
  date:      (d) -> (new Date d).toLocaleDateString()
  time:      (d) -> (new Date d).toLocaleTimeString()
  timestamp: (d) -> (new Date d).getTime()

now = -> new Date()
y2ts = (years) -> (years * 365 * 24 * 60 * 60000)
years_from_now = (years) -> new Date ((y2ts years) + date_to.timestamp now())
from_now = (->
  n = now().getTime()
  (t) -> new Date (t+n))()
a_week = 7*84600*1000

get_template = (name) -> _.template ($ "##{name}-template").html()
delay = (wait, fn) -> _.delay fn, wait

mimetypes = [ [('jpg jpeg png pnm gif bmp'.split ' '), 'image']
              [('avi mkv mov mpg mpeg mp4 ts flv'.split ' '), 'video']]
get_mimetype = (filename) =>
  ext = (_.last filename.split '.').toLowerCase()
  mt = _.find mimetypes, (mt) -> ext in mt[0]
  if mt then mt[1] else null

url_test = (v) -> /(http|ftp|https):\/\/[\w-]+(\.[\w-]+)+([\w.,@?^=%&amp;:\/~+#-]*[\w@?^=%&amp;\/~+#-])?/.test v
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
    start_date: now()
    end_date: from_now a_week
    duration: default_duration
    is_enabled: 0
    nocache: 0

API.Assets = class Assets extends Backbone.Collection
  url: "/api/assets"
  model: Asset


# Views
class EditAssetView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']" # get field element
  $fv: (field, val...) => (@$f field).val val... # get or set filed value

  initialize: (options) =>
    @edit = options.edit
    ($ 'body').append @$el.html get_template 'asset-modal'
    (@$ 'input.time').timepicker
      minuteStep: 5, showInputs: yes, disableFocus: yes, showMeridian: yes

    (@$ 'input[name="nocache"]').prop 'checked', @model.get 'nocache'
    (@$ '.modal-header .close').remove()
    (@$el.children ":first").modal()
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

    has_nocache = ((@$ '#tab-uri').hasClass 'active') and (@model.get 'mimetype') is 'image'
    (@$ '.nocache').toggle has_nocache

    (@$ '.duration').toggle ((@model.get 'mimetype') != 'video')
    @clickTabNavUri() if (@model.get 'mimetype') == 'webpage'

    for field in @model.fields
      if (@$fv field) != @model.get field
        @$fv field, @model.get field
    (@$ '.uri-text').html insertWbr @model.get 'uri'

    for which in ['start', 'end']
      date = @model.get "#{which}_date"
      @$fv "#{which}_date_date", date_to.date date
      (@$f "#{which}_date_date").datepicker autoclose: yes
      (@$f "#{which}_date_date").datepicker 'setValue', date_to.date date
      @$fv "#{which}_date_time", date_to.time date
    @delegateEvents()
    no

  viewmodel: =>
    for which in ['start', 'end']
      @$fv "#{which}_date", date_to.iso do =>
        (@$fv "#{which}_date_date") + " " + (@$fv "#{which}_date_time")
    for field in @model.fields when not (@$f field).prop 'disabled'
      @model.set field, (@$fv field), silent:yes

  events:
    'submit form': 'save'
    'click .cancel': 'cancel'
    'change': 'change'
    'keyup': 'change'
    'click .tabnav-uri': 'clickTabNavUri'
    'click .tabnav-file_upload': 'clickTabNavUpload'
    'paste [name=uri]': 'updateUriMimetype'
    'change [name=file_upload]': 'updateFileUploadMimetype'

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
    @model.set @model.previousAttributes()
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


class AssetRowView extends Backbone.View
  tagName: "tr"

  initialize: (options) =>
    @template = get_template 'asset-row'

  render: =>
    @$el.html @template _.extend json = @model.toJSON(),
      name: insertWbr json.name # word break urls at slashes
      start_date: date_to.string json.start_date
      end_date: date_to.string json.end_date
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


class AssetsView extends Backbone.View
  initialize: (options) =>
    @collection.bind event, @render for event in ('reset add remove sync'.split ' ')

  render: =>
    (@$ "##{which}-assets").html '' for which in ['active', 'inactive']

    @collection.each (model) =>
      which = if model.get 'is_active' then 'active' else 'inactive'
      (@$ "##{which}-assets").append (new AssetRowView model: model).render()

    for which in ['inactive', 'active']
      @$(".#{which}-table thead").toggle !!(@$("##{which}-assets tr").length)
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


jQuery -> API.app = new App el: $ 'body'

