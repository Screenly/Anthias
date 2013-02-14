### screenly-ose ui ###

API = (window.Screenly ||= {}) # exports
API.date_to = date_to =
  iso:       (d) -> (new Date d).toISOString()
  string:    (d) -> (moment (new Date d)).format("MM/DD/YYYY hh:mm:ss A")
  time:      (d) -> (new Date d).toLocaleTimeString()
  timestamp: (d) -> (new Date d).getTime()

now = -> new Date()
y2ts = (years) -> (years * 365 * 24 * 60 * 60000)
years_from_now = (years) -> new Date ((y2ts years) + date_to.timestamp now())

get_template = (name) -> _.template ($ "##{name}-template").html()


# Models

# Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = on

class Asset extends Backbone.Model
  idAttribute: "asset_id"

class Assets extends Backbone.Collection
  url: "/api/assets"
  model: Asset


# Views

class AssetModalView extends Backbone.View
  $f: (field) => @$ "[name='#{field}']"
  $fv: (field, val...) => (@$f field).val val...

  initialize: (options) =>
    @template = get_template 'asset-modal'
    ($ 'body').append @render().el
    (@$el.children ":first").modal()

  render: =>
    @$el.html @template()

    (@$ "input.date").datepicker autoclose: yes
    (@$ 'input.time').timepicker
      minuteStep: 5
      defaultTime: 'current'
      showInputs: yes
      disableFocus: yes
      showMeridian: yes

    (@$ '#modalLabel').text (if @model then "Edit Asset" else "Add Asset")

    if @model
      (@$ "form").attr "action", @model.url()

      for field in 'name uri duration mimetype'.split ' '
        @$fv field, @model.get field

      for which in ['start', 'end']
        (@$f "#{which}_date_date").datepicker 'update', @model.get "#{which}_date"
        @$fv "#{which}_date_time", date_to.time @model.get "#{which}_date"

    else
      (@$ "input.date").datepicker 'update', new Date()

    @changeMimetype()
    this

  events:
    'click #submit-button': 'submit'
    'change select[name=mimetype]': 'changeMimetype'

  submit: (e) =>
    for which in ['start', 'end']
      @$fv "#{which}_date",
        date_to.iso (@$fv "#{which}_date_date") + " " + (@$fv "#{which}_date_time")
    (@$ "form").submit()

  changeMimetype: =>
    (@$ '.file_upload').toggle ((@$fv 'mimetype') != 'webpage')



class AssetRowView extends Backbone.View
  tagName: "tr"

  initialize: (options) =>
    @template = get_template 'asset-row'

  render: =>
    @$el.html @template @model.toJSON()
    (@$ ".toggle input").prop "checked", @model.get 'is_active'

    switch (@model.get "mimetype")
      when "video"   then icon_class = "icon-facetime-video"
      when "image"   then icon_class = "icon-picture"
      when "webpage" then icon_class = "icon-globe"
      else                icon_class = ""
    (@$ ".asset-icon").addClass icon_class

    (@$ "#delete-asset-button").popover
      html: yes, placement: 'left', title: "Are you sure?", content: get_template 'confirm-delete'
    this

  events:
    'click #activation-toggle': 'toggleActive'
    'click #edit-asset-button': 'edit'
    'click #confirm-delete': 'delete'
    'click #cancel-delete': 'hidePopover'

  toggleActive: (e) =>
    if @model.get 'is_active'
      @model.set
        is_active: no
        end_date: date_to.iso now()
    else
      @model.set
        is_active: yes
        start_date: date_to.iso now()
        end_date: date_to.iso years_from_now 10
    @model.save()
    (@$ ".toggle input").prop "checked", @model.get 'is_active'
    setTimeout (=> @remove()), 300
    e.preventDefault(); false

  edit: (e) =>
    new AssetModalView model: @model
    e.preventDefault(); false

  delete: (e) =>
    @hidePopover()
    @model.destroy().done => @remove()
    e.preventDefault(); false

  hidePopover: ->
    (@$ "#delete-asset-button").popover 'hide'


class AssetsView extends Backbone.View
  initialize: (options) =>
    for event in ['reset', 'add']
      @collection.bind event, @render

    @collection.bind 'change:is_active', (model) =>
      setTimeout (=> @render _ [model]), 320

  render: (models = @collection) =>
    models.each (model) =>
      which = if model.get 'is_active' then 'active' else 'inactive'
      (@$ "##{which}-assets").append (new AssetRowView model: model).render().el

    for which in ['inactive', 'active']
      header = @$(".#{which}-table thead")
      if @$("##{which}-assets tr").length
        header.show()
      else
        header.hide()

    @


# App

API.app = class App extends Backbone.View
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
    new AssetModalView()
    e.preventDefault(); false


jQuery -> new App el: $ 'body'
