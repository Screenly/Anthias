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


# Models

# Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = on

API.Asset = class Asset extends Backbone.Model
  idAttribute: "asset_id"
  fields: => _.keys @defaults()
  defaults: =>
    name: ''
    mimetype: 'image'
    uri: ''
    start_date: now()
    end_date: now()
    duration: 10

API.Assets = class Assets extends Backbone.Collection
  url: "/api/assets"
  model: Asset


# Views

class EditAssetView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']"
  $fv: (field, val...) => (@$f field).val val...

  initialize: (options) =>
    ($ 'body').append @$el.html get_template 'asset-modal'
    (@$ 'input.time').timepicker
      minuteStep: 5, showInputs: yes, disableFocus: yes, showMeridian: yes

    (@$ '.modal-header .close').remove()
    (@$el.children ":first").modal()
    @model.bind 'change', @render
    @render()
    this

  render: (model = @model) =>
    @undelegateEvents()
    # errors = @model.validate @model.attributes

    (@$ '#modalLabel').text "Edit Asset" unless model.isNew()
    (@$ '.duration').toggle ((model.get 'mimetype') != 'video')

    if (model.get 'mimetype') == 'webpage'
      (@$ 'li.tabnav-uri').click()

    for field in model.fields()
      @$fv field, model.get field

    for which in ['start', 'end']
      date = model.get "#{which}_date"
      @$fv "#{which}_date_date", date_to.date date
      (@$f "#{which}_date_date").datepicker autoclose: yes
      (@$f "#{which}_date_date").datepicker 'setValue', date_to.date date
      @$fv "#{which}_date_time", date_to.time date

    @delegateEvents()
    false

  viewmodel: =>
    for which in ['start', 'end']
      @$fv "#{which}_date", date_to.iso do =>
        (@$fv "#{which}_date_date") + " " + (@$fv "#{which}_date_time")
    for field in @model.fields()
      @model.set field, (@$fv field), silent: true
    if (@$ 'li.tabnav-file_upload').hasClass 'active'
      @model.set 'uri', @$fv 'file_upload'


  events:
    'submit form': 'save'
    'click .cancel': 'cancel'
    'change': 'change'
    'keyup': 'change'
    'click .tabnav-uri': 'clickTabNavUri'
    'click .tabnav-file_upload': 'clickTabNavUpload'

  save: (e) =>
    e.preventDefault()
    @viewmodel()
    isNew = @model.isNew()
    #if @model.isValid()

    save = null
    if (@$ '#tab-file_upload').hasClass 'active'
      @$el.fileupload
        url: @model.url()
        progressall: (e, data) => console.log 'prog', e, data
        done: (e, data) => console.log 'prg done'
      save = @$el.fileupload 'send',
        fileInput: (@$f 'file_upload')
        formData: (form) => console.log form.serializeArray(); form.serializeArray()

    else
      save = @model.save()

    save.done (data) =>
      @collection.add @model if not @model.collection
      (@$el.children ":first").modal 'hide'
      _.extend @model.attributes, data
      @model.collection.add @model if isNew
    save.fail =>
      console.log 'fail'
      (@$ 'input, select').prop 'disabled', off

    (@$ 'input, select').prop 'disabled', on
    false

  change: (e) =>
    @_change  ||= _.throttle (=>
      @viewmodel()
      @model.trigger 'change'
      true), 500
    @_change arguments...

  cancel: (e) =>
    @model.set @model.previousAttributes()
    if @model.isNew() then @model.destroy()
    (@$el.children ":first").modal 'hide'
    delay 500, => @remove()

  clickTabNavUri: (e) =>
    (@$ 'ul.nav-tabs li').removeClass 'active'
    (@$ '.tab-pane').removeClass 'active'
    (@$ '.tabnav-uri').addClass 'active'
    (@$ '#tab-uri').addClass 'active'
    _.defer @change
    false

  clickTabNavUpload: (e) =>
    (@$ 'ul.nav-tabs li').removeClass 'active'
    (@$ '.tab-pane').removeClass 'active'
    (@$ '.tabnav-file_upload').addClass 'active'
    (@$ '#tab-file_upload').addClass 'active'
    _.defer @change
    false

class AssetRowView extends Backbone.View
  tagName: "tr"

  initialize: (options) =>
    @template = get_template 'asset-row'

  render: =>
    @$el.html @template @model.toJSON()
    (@$ ".delete-asset-button").popover content: get_template 'confirm-delete'
    (@$ ".toggle input").prop "checked", @model.get 'is_active'
    (@$ ".asset-icon").addClass switch @model.get "mimetype"
      when "video"   then "icon-facetime-video"
      when "image"   then "icon-picture"
      when "webpage" then "icon-globe"
      else ""
    this

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
        @model.set @model.previousAttributes(), silent: yes # revert changes
        @setEnabled on
        @render()
    true

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
    false

  delete: (e) =>
    @hidePopover()
    if (xhr = @model.destroy()) is not false
      xhr.done => @remove()
    else
      @remove()
    false

  showPopover: =>
    if not ($ '.popover').length
      (@$ ".delete-asset-button").popover 'show'
      ($ '.confirm-delete').click @delete
      ($ window).one 'click', @hidePopover
    false

  hidePopover: =>
    (@$ ".delete-asset-button").popover 'hide'
    false


class AssetsView extends Backbone.View
  initialize: (options) =>
    @collection.bind event, @render for event in ['reset', 'add', 'sync']

  render: =>
    (@$ "##{which}-assets").html '' for which in ['active', 'inactive']

    @collection.each (model) =>
      which = if model.get 'is_active' then 'active' else 'inactive'
      (@$ "##{which}-assets").append (new AssetRowView model: model).render().el

    for which in ['inactive', 'active']
      @$(".#{which}-table thead").toggle !!(@$("##{which}-assets tr").length)
    this


API.App = class App extends Backbone.View
  initialize: =>
    ($ window).ajaxError =>
      ($ '#request-error').html (get_template 'request-error')()

    (API.assets = new Assets()).fetch()
    API.assetsView = new AssetsView
      collection: API.assets
      el: @$ '#assets'
    this

  events: {'click #add-asset-button': 'add'}

  add: (e) =>
    new EditAssetView model:
      new Asset {}, {collection: API.assets}
    false


jQuery -> API.app = new App el: $ 'body'

