### screenly-ose ui ###

API = (window.Screenly ||= {}) # exports

D = (d) -> new Date d # parses strings and timestamps; idempotent
now = -> new Date()

API.d2iso  = d2iso  = (d) -> (D d).toISOString()        # isostring
API.d2s    = d2s    = (d) -> (D d).toLocaleString()     # nice string
API.d2time = d2time = (d) -> (D d).toLocaleTimeString() # nice time
API.d2ts   = d2ts   = (d) -> (D d).getTime()            # timestamp

year2ts = (years) -> (years * 365 * 24 * 60 * 60000)
years_from_now = (years) -> D (year2ts years) + d2ts now()

_tpl = (name) -> _.template ($ "##{name}-template").html()


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
    @tpl = _tpl 'asset-modal'
    ($ 'body').append @render().el
    (@$el.children ":first").modal()

  render: =>
    @$el.html @tpl()

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
        @$fv "#{which}_date_time", d2time @model.get "#{which}_date"

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
        d2iso (@$fv "#{which}_date_date") + " " + (@$fv "#{which}_date_time")
    (@$ "form").submit()

  changeMimetype: =>
    console.log 'chaneg'
    (@$ '.file_upload').toggle ((@$fv 'mimetype') != 'webpage')



class AssetRowView extends Backbone.View
  tagName: "tr"

  initialize: (options) =>
    @tpl = _tpl 'asset-row'

  render: =>
    @$el.html @tpl @model.toJSON()
    (@$ ".toggle input").prop "checked", @model.get 'is_active'
    (@$ "#delete-asset-button").popover
      html: yes, placement: 'left', title: "Are you sure?", content: _tpl 'confirm-delete'
    this

  events:
    'click #activation-toggle': 'toggleActive'
    'click #edit-asset-button': 'edit'
    'click #confirm-delete': 'delete'

  toggleActive: (e) =>
    if @model.get 'is_active'
      @model.set
        is_active: no
        end_date: d2iso now()
    else
      @model.set
        is_active: yes
        start_date: d2iso now()
        end_date: d2iso years_from_now 10
    @model.save()
    (@$ ".toggle input").prop "checked", @model.get 'is_active'
    setTimeout (=> @remove()), 300
    e.preventDefault(); false

  edit: (e) =>
    new AssetModalView model: @model
    e.preventDefault(); false

  delete: (e) =>
    (@$ "#delete-asset-button").popover 'hide'
    @model.destroy().done => @remove()
    e.preventDefault(); false


class AssetsView extends Backbone.View
  initialize: (options) =>
    for event in ['reset', 'add']
      @collection.bind event, @render

    @collection.bind 'change', (model) =>
      setTimeout (=> @render _ [model]), 320

  render: (models = @collection) =>
    models.each (model) =>
      which = if model.get 'is_active' then 'active' else 'inactive'
      (@$ "##{which}-assets").append (new AssetRowView model: model).render().el
    this


# App

API.app = class App extends Backbone.View
  initialize: =>
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
