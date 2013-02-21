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

# Models

default_duration = 10

# Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = on

API.Asset = class Asset extends Backbone.Model
  idAttribute: "asset_id"
  fields: 'name mimetype uri start_date end_date duration'.split ' '
  defaults: =>
    name: ''
    mimetype: 'webpage'
    uri: ''
    start_date: now()
    end_date: now()
    duration: default_duration

API.Assets = class Assets extends Backbone.Collection
  url: "/api/assets"
  model: Asset


# Views

class EditAssetView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']" # get field element
  $fv: (field, val...) => (@$f field).val val... # get or set filed value

  initialize: (options) =>
    ($ 'body').append @$el.html get_template 'asset-modal'
    (@$ 'input.time').timepicker
      minuteStep: 5, showInputs: yes, disableFocus: yes, showMeridian: yes

    (@$ '.modal-header .close').remove()
    (@$el.children ":first").modal()
    @model.bind 'change', @render
    @render()

  render: () =>
    @undelegateEvents()

    if not @model.isNew()
      (@$ f).attr 'disabled', on for f in 'mimetype uri file_upload'.split ' '
      (@$ '#modalLabel').text "Edit Asset"
      (@$ '.asset-location').hide(); (@$ '.asset-location.edit').show()

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
    isNew = @model.isNew()
    save = null
    if (@$ '#tab-file_upload').hasClass 'active'
      (@$ '.progress').show()
      @$el.fileupload
        url: @model.url()
        progressall: (e, data) => if data.loaded and data.total
          (@$ '.progress .bar').css 'width', "#{data.loaded/data.total*100}%"
      save = @$el.fileupload 'send', fileInput: (@$f 'file_upload')
    else
      save = @model.save()

    (@$ 'input, select').prop 'disabled', on
    save.done (data) =>
      default_duration = @model.get 'duration'
      @collection.add @model if not @model.collection
      (@$el.children ":first").modal 'hide'
      _.extend @model.attributes, data
      @model.collection.add @model if isNew
    save.fail =>
      (@$ '.progress').hide()
      (@$ 'input, select').prop 'disabled', off
    no

  change: (e) =>
    @_change  ||= _.throttle (=>
      @viewmodel()
      @model.trigger 'change'
      yes), 500
    @_change arguments...

  cancel: (e) =>
    @model.set @model.previousAttributes()
    if @model.isNew() then @model.destroy()
    (@$el.children ":first").modal 'hide'

  clickTabNavUri: (e) => # TODO: clean
    if not (@$ '#tab-uri').hasClass 'active'
      (@$ 'ul.nav-tabs li').removeClass 'active'
      (@$ '.tab-pane').removeClass 'active'
      (@$ '.tabnav-uri').addClass 'active'
      (@$ '#tab-uri').addClass 'active'
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
    mt = get_mimetype filename
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
    (@$ ".toggle input").prop "checked", @model.get 'is_active'
    (@$ ".asset-icon").addClass switch @model.get "mimetype"
      when "video"   then "icon-facetime-video"
      when "image"   then "icon-picture"
      when "webpage" then "icon-globe"
      else ""
    @el

  events:
    'change .activation-toggle input': 'toggleActive'
    'click .edit-asset-button': 'edit'
    'click .delete-asset-button': 'showPopover'

  toggleActive: (e) =>
    if @model.get 'is_active' then @model.set
      is_active: no
      end_date: date_to.iso now()
    else @model.set
      is_active: yes
      start_date: date_to.iso now()
      end_date: date_to.iso years_from_now 10

    @setEnabled off
    save = @model.save()
    delay 300, =>
      save.done =>
        @remove()
        @model.collection.trigger 'add', _ [@model]
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


class AssetsView extends Backbone.View
  initialize: (options) =>
    @collection.bind event, @render for event in ['reset', 'add', 'sync']

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
    ($ window).ajaxError =>
      ($ '#request-error').html (get_template 'request-error')()

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

