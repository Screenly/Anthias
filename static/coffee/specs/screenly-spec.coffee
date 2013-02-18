
describe "Screenly Open Source", ->
  
  it "should have a screenly object at its root", ->
    expect(screenly).toBeDefined()

  it "should have an instance of Assets on the screenly object", ->
    expect(screenly.Assets).toBeDefined()
    expect(screenly.Assets).toEqual         jasmine.any(screenly.collections.Assets)
    expect(screenly.ActiveAssets).toEqual   jasmine.any(screenly.collections.ActiveAssets)
    expect(screenly.InactiveAssets).toEqual jasmine.any(screenly.collections.InactiveAssets)
  
  describe "Models", ->

    it "should exist", ->
      expect(screenly.models).toBeDefined()

    describe "Asset model", ->
      it "should exist", ->
        expect(screenly.models.Asset).toBeDefined()

  describe "Collections", ->

    it "should exist", ->
      expect(screenly.collections).toBeDefined()
    
    describe "Assets", ->
      it "should exist", ->
        expect(screenly.collections.Assets).toBeDefined()
        expect(screenly.collections.ActiveAssets).toBeDefined()
        expect(screenly.collections.InactiveAssets).toBeDefined()

      it "should use the Asset model", ->
        assets = new screenly.collections.Assets()
        expect(assets.model).toBe screenly.models.Asset

      it "should populate ActiveAssets and InactiveAssets when fetched", ->
        screenly.Assets.reset [
            {name: "zacharytamas.com", mimetype:"webpage", is_active: true},
        ]

        # ActiveAssets should have one model now
        expect(screenly.ActiveAssets.models.length).toEqual 1

        # InactiveAssets should still be empty
        expect(screenly.InactiveAssets.models.length).toEqual 0

        # Now make the page inactive and confirm that ActiveAssets
        # is empty (the previous information is wiped away on a
        # new data load) and the InactiveAssets collection contains
        # the new asset.

        screenly.Assets.reset [
            {name: "zacharytamas.com", mimetype:"webpage", is_active: false},
        ]

        # ActiveAssets should be empty now
        expect(screenly.ActiveAssets.models.length).toEqual 0

        # InactiveAssets should have a model
        expect(screenly.InactiveAssets.models.length).toEqual 1

        screenly.Assets.reset [
            {name: "zacharytamas.com", mimetype:"webpage", is_active: false},
            {name: "Hacker News", mimetype: "webpage", is_active: true}
        ]

        # They should both have a model now
        expect(screenly.ActiveAssets.models.length).toEqual 1
        expect(screenly.InactiveAssets.models.length).toEqual 1
        expect(screenly.Assets.models.length).toEqual 2

  describe "Views", ->

    it "should exist", ->
      expect(screenly.views).toBeDefined()
