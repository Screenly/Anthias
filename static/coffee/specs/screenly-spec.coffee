
describe "Screenly Open Source", ->
  
  it "should have a Screenly object at its root", ->
    expect(Screenly).toBeDefined()


  describe "date_to", ->

    testDate = new Date(2014, 5, 6, 14, 20, 0, 0);
    dd = Screenly.date_to(testDate);
    
    it "should format date and time as 'MM/DD/YYYY hh:mm:ss A'", ->  
      expect(dd.string()).toBe '06/06/2014 02:20:00 PM'
    
    it "should format date as 'MM/DD/YYYY'", ->      
      expect(dd.date()).toBe '06/06/2014'
    
    it "should format date as 'hh:mm:ss A'", ->            
      expect(dd.time()).toBe '02:20 PM'


  describe "Models", ->

    describe "Asset model", ->
      it "should exist", ->
        expect(Screenly.Asset).toBeDefined()


  describe "Collections", ->

    describe "Assets", ->
      it "should exist", ->
        expect(Screenly.Assets).toBeDefined()

      it "should use the Asset model", ->
        assets = new Screenly.Assets()
        expect(assets.model).toBe Screenly.Asset


  describe "Views", ->

    it "should have EditAssetView", ->
      expect(Screenly.View.EditAssetView).toBeDefined()

    it "should have AssetRowView", ->
      expect(Screenly.View.AssetRowView).toBeDefined()

    it "should have AssetsView", ->
      expect(Screenly.View.AssetsView).toBeDefined()