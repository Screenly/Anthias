
@screenly = window.screenly ? {}
@screenly.collections = window.screenly.collections ? {}

jQuery ->

  ################################
  # Models
  ################################

  class Asset extends Backbone.Model

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

  class ActiveAssets extends Backbone.Collection
    model: Asset

  class InactiveAssets extends Backbone.Collection
    model: Asset

  screenly.collections.Assets = Assets
  screenly.collections.ActiveAssets = ActiveAssets
  screenly.collections.InactiveAssets = InactiveAssets

  screenly.ActiveAssets = new ActiveAssets()
  screenly.InactiveAssets = new InactiveAssets()
  (screenly.Assets = new Assets()).fetch()

