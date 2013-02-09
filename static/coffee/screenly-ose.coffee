
@screenly = window.screenly ? {}
@screenly.collections = window.screenly.collections ? {}
@screenly.views = window.screenly.views ? {}
@screenly.models = window.screenly.models ? {}
@screenly.utils = window.screenly.utils ? {}


# Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = true


################################
# Utilities
################################

ISOFromDateString = (string) ->
  (new Date(string)).toISOString()

formattedDateString = (date) ->
  (new Date(date)).toLocaleString()

futureDateInYears = (years) ->
  new Date(new Date().getTime() + (years * 365 * 24 * 60 * 60000))

@screenly.utils.formattedDateString = formattedDateString


################################
# Models
################################

class Asset extends Backbone.Model

  initialize: (options) ->
    if @get("asset_id")
      @set('id', @get('asset_id'))

  url: ->
    if @get('asset_id')
      "/api/assets/#{@get('asset_id')}"

screenly.models.Asset = Asset

################################
# Collections
################################

class Assets extends Backbone.Collection
  url: "/api/assets"
  model: Asset
  
  initialize: (options) ->
    @on "reset", ->
      screenly.ActiveAssets.reset()
      screenly.InactiveAssets.reset()

      @each (model) ->
        if model.get('is_active')
          screenly.ActiveAssets.add model
        else
          screenly.InactiveAssets.add model

screenly.Assets = new Assets()

class ActiveAssets extends Backbone.Collection
  model: Asset

class InactiveAssets extends Backbone.Collection
  model: Asset

screenly.collections.Assets = Assets
screenly.collections.ActiveAssets = ActiveAssets
screenly.collections.InactiveAssets = InactiveAssets

screenly.ActiveAssets = new ActiveAssets()
screenly.InactiveAssets = new InactiveAssets()

################################
# Views
################################

class AssetModalView extends Backbone.View

  initialize: (options) ->
    @template = _.template($('#asset-modal-template').html())

  events:
    'click #submit-button': 'submitButtonWasClicked'

  render: ->
    $(@el).html(@template())

    @$('input.time').timepicker({
      minuteStep: 5,
      showInputs: false,
      disableFocus: true,
      defaultTime: 'current',
      showMeridian: true
    })

    if @model

      @$('#modalLabel').text("Edit Asset")
      @$("form").attr "action", "/api/assets/#{@model.get('asset_id')}"
      @$("#submit-button").val("Edit Asset")

      @$("input[name='name']").val @model.get('name')
      @$("input[name='uri']").val @model.get('uri')
      @$("input[name='duration']").val @model.get('duration')
      @$("select[name='mimetype']").val @model.get('mimetype')

      start_date = new Date(@model.get('start_date'))
      end_date = new Date(@model.get('end_date'))
      
      @$("input[name='start_date_date']").datepicker('update', start_date)
      @$("input[name='end_date_date']").datepicker('update', end_date)
      @$("input[name='start_date_time']").val start_date.toLocaleTimeString()
      @$("input[name='end_date_time']").val end_date.toLocaleTimeString()

    else
      @$('#modalLabel').text("Add Asset")
      @$("input.date").datepicker {autoclose: true}
      @$("input.date").datepicker 'update', new Date()

    @

  submitButtonWasClicked: (event) ->
    event.preventDefault()

    start_date = $("input[name='start_date_date']").val() + " " + $("input[name='start_date_time']").val()
    end_date = $("input[name='end_date_date']").val() + " " + $("input[name='end_date_time']").val()

    $("input[name='start_date']").val(ISOFromDateString(start_date))
    $("input[name='end_date']").val(ISOFromDateString(end_date))

    @$("form").submit()

screenly.views.AssetModalView = AssetModalView

class AssetsView extends Backbone.View
  initialize: (options) ->

    if not 'templateName' in options
      console.log "You need to specify the template name for this AssetsView."

    @template = _.template($('#' + options.templateName).html())
    
    @collection.bind "reset", @render, @
    @collection.bind "remove", @render, @
    @collection.bind "add", @render, @

  render: ->
    $(@el).html(@template())

    # TODO This can be cleaned up to not re-render everything all the time.
    
    @$('tbody').empty()
    @collection.each (asset) =>
      @$('tbody').append (new AssetRowView({model: asset})).render().el

    @

class AssetRowView extends Backbone.View
  
  initialize: (options) ->
    @template = _.template($('#asset-row-template').html())

  events:
    'click #activation-toggle': 'toggleActivation'
    'click #edit-asset-button': 'editAsset'
    #'click #delete-asset-button': 'deleteAsset'

  tagName: "tr"

  render: ->
    $(@el).html(@template(@model.toJSON()))
    if @model.get('is_active')
      @$(".toggle input").prop("checked", true)
    @

  toggleActivation: (event) ->

    # If it is active, let's deactivate it.
    if @model.get('is_active')
      
      # To deactivate, set this asset's end_date to right now
      @model.set('end_date', (new Date()).toISOString())

      # Now persist the change on the server so this becomes
      # active immediately.
      @model.save()

      # Now let's update the local collections, which
      # should change the view the user sees. Let's delay
      # this for 1 second to allow the animation to
      # complete.
      setTimeout (=> 
        screenly.ActiveAssets.remove(@model)
        screenly.InactiveAssets.add(@model)
      ), 500

    else
      # To "activate" an asset, we set its start_date
      # to now and, for now, set its end_date to
      # 10 years from now.
      @model.set('start_date', (new Date()).toISOString())
      @model.set('end_date', futureDateInYears(10).toISOString())
      @model.save()

      # Now let's update the local collections, which
      # should change the view the user sees.
      setTimeout (=> 
        screenly.InactiveAssets.remove @model
        screenly.ActiveAssets.add @model
      ), 500

    

  editAsset: (event) ->
    event.preventDefault()
    modal = new AssetModalView({model: @model})
    $(@el).append modal.render().el
    $(modal.el).children(":first").modal()

  


screenly.views.AssetsView = AssetsView

jQuery ->
  
  screenly.Assets.fetch()

  # Initialize the initial view
  activeAssetsView = new AssetsView(
    collection: screenly.ActiveAssets, 
    templateName: "active-assets-template"
  )

  inactiveAssetsView = new AssetsView(
    collection: screenly.InactiveAssets,
    templateName: "inactive-assets-template"
  )

  $("#active-assets-container").append activeAssetsView.render().el
  $("#inactive-assets-container").append inactiveAssetsView.render().el

  $("#add-asset-button").click (event) ->
    event.preventDefault()
    modal = new AssetModalView()
    $("body").append modal.render().el
    $(modal.el).children(":first").modal()
