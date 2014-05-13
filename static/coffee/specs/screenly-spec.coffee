
describe "Screenly Open Source", ->
  
  it "should have a Screenly object at its root", ->
    expect(Screenly).toBeDefined()


  describe "date_to", ->

    test_date = new Date(2014, 5, 6, 14, 20, 0, 0);
    a_date = Screenly.date_to(test_date);
    
    it "should format date and time as 'MM/DD/YYYY hh:mm:ss A'", ->  
      expect(a_date.string()).toBe '06/06/2014 02:20:00 PM'
    
    it "should format date as 'MM/a_date/YYYY'", ->      
      expect(a_date.date()).toBe '06/06/2014'
    
    it "should format date as 'hh:mm:ss A'", ->            
      expect(a_date.time()).toBe '02:20 PM'


  describe "Models", ->

    describe "Asset model", ->
      it "should exist", ->
        expect(Screenly.Asset).toBeDefined()

      start_date = new Date(2014, 4, 6, 14, 20, 0, 0);
      end_date = new Date();
      end_date.setMonth(end_date.getMonth() + 2) 
      asset = new Screenly.Asset({
        asset_id: 2
        duration: "8"
        end_date: end_date
        is_enabled: true
        mimetype: 'webpage'
        name: 'Test'
        start_date: start_date
        uri: 'http://www.screenlyapp.com'
      })

      it "should be active if enabled and date is in range", ->
        expect(asset.active()).toBe true

      it "should be inactive if disabled and date is in range", ->
        asset.set 'is_enabled', false
        expect(asset.active()).toBe false

      it "should be inactive if enabled and date is out of range", ->
        asset.set 'is_enabled', true
        asset.set 'start_date', asset.get 'end_date'
        expect(asset.active()).toBe false

      it "should rollback to backup data if it exists", ->

        asset.set 'start_date', start_date
        asset.set 'end_date', end_date
        asset.backup()

        asset.set({
          is_enabled: false
          name: "Test 2"
          start_date: new Date(2011, 4, 6, 14, 20, 0, 0)
          end_date: new Date(2011, 4, 6, 14, 20, 0, 0)
          uri: "http://www.wireload.net"               
        })

        asset.rollback()

        expect(asset.get 'is_enabled').toBe true        
        expect(asset.get 'name').toBe 'Test'        
        expect(asset.get 'start_date').toBe start_date        
        expect(asset.get 'uri').toBe "http://www.screenlyapp.com"

      it "should erase backup date after rollback", ->
        asset.set({
          is_enabled: false
          name: "Test 2"
          start_date: new Date(2011, 4, 6, 14, 20, 0, 0)
          end_date: new Date(2011, 4, 6, 14, 20, 0, 0)
          uri: "http://www.wireload.net"               
        })

        asset.rollback()

        expect(asset.get 'is_enabled').toBe false        
        expect(asset.get 'name').toBe 'Test 2'        
        expect(asset.get('start_date').toISOString()).toBe (new Date(2011, 4, 6, 14, 20, 0, 0)).toISOString()
        expect(asset.get 'uri').toBe "http://www.wireload.net"        


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