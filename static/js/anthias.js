/*
 * decaffeinate suggestions:
 * DS002: Fix invalid constructor
 * DS101: Remove unnecessary use of Array.from
 * DS102: Remove unnecessary code created because of implicit returns
 * DS103: Rewrite code to no longer use __guard__, or convert again using --optional-chaining
 * DS203: Remove `|| {}` from converted for-own loops
 * DS205: Consider reworking code to avoid use of IIFEs
 * DS206: Consider reworking classes to avoid initClass
 * DS207: Consider shorter variations of null checks
 * Full docs: https://github.com/decaffeinate/decaffeinate/blob/main/docs/suggestions.md
 */
/* anthias ui */

let AddAssetView, App, Asset, AssetRowView, Assets, AssetsView, date_to, EditAssetView;
import '../sass/anthias.scss';

$().ready(() => $('#subsribe-form-container').popover({content: get_template('subscribe-form')}));


const API = (window.Anthias || (window.Anthias = {})); // exports

const dateSettings = {};

if (use24HourClock) {
  dateSettings.time = "HH:mm";
  dateSettings.fullTime = "HH:mm:ss";
  dateSettings.showMeridian = false;
} else {
  dateSettings.time = "hh:mm A";
  dateSettings.fullTime = "hh:mm:ss A";
  dateSettings.showMeridian = true;
}

dateSettings.date = dateFormat.toUpperCase();
dateSettings.datepickerFormat = dateFormat;

dateSettings.fullDate = `${dateSettings.date} ${dateSettings.fullTime}`;


API.date_to = (date_to = function(d) {
  // Cross-browser UTC to localtime conversion
  const dd = moment.utc(d).local();
  return {
    string() { return dd.format(dateSettings.fullDate); },
    date() { return dd.format(dateSettings.date); },
    time() { return dd.format(dateSettings.time); }
  };
});

const now = () => new Date();

var get_template = name => _.template(($(`#${name}-template`)).html());
const delay = (wait, fn) => _.delay(fn, wait);

const mimetypes = [ [('jpe jpg jpeg png pnm gif bmp'.split(' ')), 'image'],
              [('avi mkv mov mpg mpeg mp4 ts flv'.split(' ')), 'video']];
const viduris   = ('rtsp rtmp'.split(' '));
const domains = [ [('www.youtube.com youtu.be'.split(' ')), 'youtube_asset']];


const getMimetype = function(filename) {
  const scheme = (_.first(filename.split(':'))).toLowerCase();
  const match = Array.from(viduris).includes(scheme);
  if (match) {
    return 'streaming';
  }

  const domain = (_.first(((_.last(filename.split('//'))).toLowerCase()).split('/')));
  let mt = _.find(domains, mt => Array.from(mt[0]).includes(domain));
  if (mt && Array.from(mt[0]).includes(domain)) {
    return mt[1];
  }

  const ext = (_.last(filename.split('.'))).toLowerCase();
  mt = _.find(mimetypes, mt => Array.from(mt[0]).includes(ext));
  if (mt) {
    return mt[1];
  }
};

const durationSecondsToHumanReadable = function(secs) {
  let hours, minutes, seconds;
  let durationString = "";
  const secInt = parseInt(secs);

  if ((hours = Math.floor(secInt / 3600)) > 0) {
    durationString += hours + " hours ";
  }
  if ((minutes = Math.floor(secInt / 60) % 60) > 0) {
    durationString += minutes + " min ";
  }
  if ((seconds = (secInt % 60)) > 0) {
    durationString += seconds + " sec";
  }

  return durationString;
};

const url_test = v => /(http|https|rtsp|rtmp):\/\/[\w-]+(\.?[\w-]+)+([\w.,@?^=%&amp;:\/~+#-]*[\w@?^=%&amp;\/~+#-])?/.test(v);
const get_filename = v => (v.replace(/[\/\\\s]+$/g, '')).replace(/^.*[\\\/]/g, '');
const truncate_str = v => v.replace(/(.{100})..+/, "$1...");
const insertWbr = v => (v.replace(/\//g, '/<wbr>')).replace(/\&/g, '&amp;<wbr>');

// Tell Backbone to send its saves as JSON-encoded.
Backbone.emulateJSON = false;

// Models
API.Asset = (Asset = (function() {
  Asset = class Asset extends Backbone.Model {
    constructor(...args) {
      super(...args);
      this.active = this.active.bind(this);
      this.backup = this.backup.bind(this);
      this.rollback = this.rollback.bind(this);
      this.old_name = this.old_name.bind(this);
    }

    static initClass() {
      this.prototype.idAttribute = "asset_id";
      this.prototype.fields = 'name mimetype uri start_date end_date duration skip_asset_check'.split(' ');
    }
    defaults() {
      return {
        name: '',
        mimetype: 'webpage',
        uri: '',
        is_active: 1,
        start_date: '',
        end_date: '',
        duration: defaultDuration,
        is_enabled: 0,
        is_processing: 0,
        nocache: 0,
        play_order: 0,
        skip_asset_check: 0
      };
    }
    active() {
      if (this.get('is_enabled') && this.get('start_date') && this.get('end_date')) {
        const at = now();
        const start_date = new Date(this.get('start_date'));
        const end_date = new Date(this.get('end_date'));
        return start_date <= at && at <= end_date;
      } else {
        return false;
      }
    }

    backup() {
      return this.backup_attributes = this.toJSON();
    }

    rollback() {
      if (this.backup_attributes) {
        this.set(this.backup_attributes);
        return this.backup_attributes = undefined;
      }
    }
    old_name() {
      if (this.backup_attributes) {
        return this.backup_attributes.name;
      }
    }
  };
  Asset.initClass();
  return Asset;
})());


API.Assets = (Assets = (function() {
  Assets = class Assets extends Backbone.Collection {
    static initClass() {
      this.prototype.url = "/api/v2/assets";
      this.prototype.model = Asset;
      this.prototype.comparator = 'play_order';
    }
  };
  Assets.initClass();
  return Assets;
})());


// Views
API.View = {};

API.View.AddAssetView = (AddAssetView = (function() {
  AddAssetView = class AddAssetView extends Backbone.View {
    constructor(...args) {
      super(...args);
      this.$f = this.$f.bind(this);
      this.$fv = this.$fv.bind(this);
      this.initialize = this.initialize.bind(this);
      this.viewmodel = this.viewmodel.bind(this);
      this.save = this.save.bind(this);
      this.toggleSkipAssetCheck = this.toggleSkipAssetCheck.bind(this);
      this.change_mimetype = this.change_mimetype.bind(this);
      this.clickTabNavUpload = this.clickTabNavUpload.bind(this);
      this.clickTabNavUri = this.clickTabNavUri.bind(this);
      this.updateUriMimetype = this.updateUriMimetype.bind(this);
      this.updateFileUploadMimetype = this.updateFileUploadMimetype.bind(this);
      this.updateMimetype = this.updateMimetype.bind(this);
      this.change = this.change.bind(this);
      this.validate = this.validate.bind(this);
      this.cancel = this.cancel.bind(this);
      this.destroyFileUploadWidget = this.destroyFileUploadWidget.bind(this);
    }

    static initClass() {

      this.prototype.events = {
        'change': 'change',
        'click #save-asset': 'save',
        'click .cancel': 'cancel',
        'hidden.bs.modal': 'destroyFileUploadWidget',
        'click .tabnav-uri': 'clickTabNavUri',
        'click .tabnav-file_upload': 'clickTabNavUpload',
        'change .is_enabled-skip_asset_check_checkbox': 'toggleSkipAssetCheck',
        'keyup [name=uri]': 'change'
      };
    }
    $f(field) { return $(`[name='${field}']`); } // get field element
    $fv(field, ...val) { return (this.$f(field)).val(...Array.from(val || [])); } // get or set filed value

    initialize(oprions) {
      ($('body')).append(this.$el.html(get_template('asset-modal')));
      (this.$el.children(":first")).modal();
      ($('.cancel')).val('Back to Assets');

      const deadlines = {start: now(), end: (moment().add('days', 30)).toDate()};
      for (var tag of Object.keys(deadlines || {})) {
        var deadline = deadlines[tag];
        var d = date_to(deadline);
        this.$fv(`${tag}_date_date`, d.date());
        this.$fv(`${tag}_date_time`, d.time());
      }

      return false;
    }

    viewmodel(model) {
      for (var which of ['start', 'end']) {
        this.$fv(`${which}_date`, (moment((this.$fv(`${which}_date_date`)) + " " + (this.$fv(`${which}_date_time`)), dateSettings.fullDate)).toDate().toISOString());
      }
      return (() => {
        const result = [];
        for (var field of Array.from(model.fields)) {
          if (!(this.$f(field)).prop('disabled')) {
            result.push(model.set(field, (this.$fv(field)), {silent:true}));
          }
        }
        return result;
      })();
    }

    save(e) {
      if ((this.$fv('uri')) === '') {
        return false;
      }
      if (($('#tab-uri')).hasClass('active')) {
        const model =  new Asset({}, {collection: API.assets});
        this.$fv('mimetype', '');
        this.updateUriMimetype();
        this.viewmodel(model);
        model.set({name: model.get('uri')}, {silent:true});
        const save = model.save();

        ($('input')).prop('disabled', true);
        save.done(data => {
          model.id = data.asset_id;
          (this.$el.children(":first")).modal('hide');
          _.extend(model.attributes, data);
          return model.collection.add(model);
        });
        save.fail(() => {
          ($('input')).prop('disabled', false);
          return model.destroy();
        });
      }
      return false;
    }

    toggleSkipAssetCheck(e) {
      return this.$fv('skip_asset_check', parseInt((this.$fv('skip_asset_check'))) === 1 ? 0 : 1);
    }

    change_mimetype() {
      if ((this.$fv('mimetype')) === "video") {
        return this.$fv('duration', 0);
      } else if ((this.$fv('mimetype')) === "streaming") {
        return this.$fv('duration', defaultStreamingDuration);
      } else {
        return this.$fv('duration', defaultDuration);
      }
    }

    clickTabNavUpload(e) {
      if (!($('#tab-file_upload')).hasClass('active')) {
        ($('ul.nav-tabs li')).removeClass('active show');
        ($('.tab-pane')).removeClass('active');
        ($('.tabnav-file_upload')).addClass('active show');
        ($('#tab-file_upload')).addClass('active');
        ($('.uri')).hide();
        ($('.skip_asset_check_checkbox')).hide();
        ($('#save-asset')).hide();
        const that = this;
        ($("[name='file_upload']")).fileupload({
          autoUpload: false,
          sequentialUploads: true,
          maxChunkSize: 5000000, //5 MB
          url: 'api/v1/file_asset',
          progressall: (e, data) => { if (data.loaded && data.total) {
            return ($('.progress .bar')).css('width', `${(data.loaded / data.total) * 100}%`);
          } },
          add(e, data) {
            (that.$('.status')).hide();
            (that.$('.progress')).show();

            const model =  new Asset({}, {collection: API.assets});
            const filename = data['files'][0]['name'];
            that.$fv('name', filename);
            that.updateFileUploadMimetype(filename);
            that.viewmodel(model);

            return data.submit()
            .success(function(data) {
              model.set({uri: data.uri, ext: data.ext}, {silent:true});

              const save = model.save();
              save.done(function(data) {
                model.id = data.asset_id;
                _.extend(model.attributes, data);
                return model.collection.add(model);
              });
              return save.fail(() => model.destroy());}).error(() => model.destroy());
          },
          stop(e) {
            (that.$('.progress')).hide();
            return (that.$('.progress .bar')).css('width', "0");
          },
          done(e, data) {
            (that.$('.status')).show();
            (that.$('.status')).html('Upload completed.');
            return setTimeout(() => (that.$('.status')).fadeOut('slow')
            , 5000);
          }
        });
      }
      return false;
    }

    clickTabNavUri(e) { // TODO: clean
      if (!($('#tab-uri')).hasClass('active')) {
        ($("[name='file_upload']")).fileupload('destroy');
        ($('ul.nav-tabs li')).removeClass('active show');
        ($('.tab-pane')).removeClass('active');
        ($('.tabnav-uri')).addClass('active show');
        ($('#tab-uri')).addClass('active');
        ($('#save-asset')).show();
        ($('.uri')).show();
        ($('.skip_asset_check_checkbox')).show();
        ($('.status')).hide();
        return (this.$f('uri')).focus();
      }
    }

    updateUriMimetype() { return this.updateMimetype(this.$fv('uri')); }
    updateFileUploadMimetype(filename) { return this.updateMimetype(filename); }
    updateMimetype(filename) {
      const mt = getMimetype(filename);
      this.$fv('mimetype', mt ? mt : new (Asset().defaults()['mimetype']));
      return this.change_mimetype();
    }

    change(e) {
      if (!this._change) { this._change = _.throttle((() => {
        this.validate();
        return true;
      }), 500); }
      return this._change(...arguments);
    }

    validate(e) {
      let field, v;
      const that = this;
      const validators = {
        uri(v) {
          if (v) {
            if (((that.$('#tab-uri')).hasClass('active')) && !url_test(v)) {
              return 'please enter a valid URL';
            }
          }
        }
      };
      const errors = ((() => {
        const result = [];
        for (field in validators) {
          var fn = validators[field];
          if ((v = fn((this.$fv(field))))) {
            result.push([field, v]);
          }
        }
        return result;
      })());

      ($(".form-group .help-inline.invalid-feedback")).remove();
      ($(".form-group .form-control")).removeClass('is-invalid');
      ($('[type=submit]')).prop('disabled', false);
      return (() => {
        const result1 = [];
        for ([field, v] of Array.from(errors)) {
          ($('[type=submit]')).prop('disabled', true);
          ($(`.form-group.${field} .form-control`)).addClass('is-invalid');
          result1.push(($(`.form-group.${field} .controls`)).append(
            $((`<span class='help-inline invalid-feedback'>${v}</span>`))));
        }
        return result1;
      })();
    }

    cancel(e) {
      return (this.$el.children(":first")).modal('hide');
    }

    destroyFileUploadWidget(e) {
      if (($('#tab-file_upload')).hasClass('active')) {
        return ($("[name='file_upload']")).fileupload('destroy');
      }
    }
  };
  AddAssetView.initClass();
  return AddAssetView;
})());


API.View.EditAssetView = (EditAssetView = (function() {
  EditAssetView = class EditAssetView extends Backbone.View {
    constructor(...args) {
      super(...args);
      this.$f = this.$f.bind(this);
      this.$fv = this.$fv.bind(this);
      this.initialize = this.initialize.bind(this);
      this.render = this.render.bind(this);
      this.viewmodel = this.viewmodel.bind(this);
      this.changeLoopTimes = this.changeLoopTimes.bind(this);
      this.save = this.save.bind(this);
      this.change = this.change.bind(this);
      this.validate = this.validate.bind(this);
      this.cancel = this.cancel.bind(this);
      this.toggleAdvanced = this.toggleAdvanced.bind(this);
      this.displayAdvanced = this.displayAdvanced.bind(this);
      this.setLoopDateTime = this.setLoopDateTime.bind(this);
      this.setDisabledDatepicker = this.setDisabledDatepicker.bind(this);
    }

    static initClass() {

      this.prototype.events = {
        'click #save-asset': 'save',
        'click .cancel': 'cancel',
        'change': 'change',
        'keyup': 'change',
        'click .advanced-toggle': 'toggleAdvanced'
      };
    }
    $f(field) { return $(`[name='${field}']`); } // get field element
    $fv(field, ...val) { return (this.$f(field)).val(...Array.from(val || [])); } // get or set filed value

    initialize(options) {
      ($('body')).append(this.$el.html(get_template('asset-modal')));
      ($('input.time')).timepicker({
        minuteStep: 5, showInputs: true, disableFocus: true, showMeridian: dateSettings.showMeridian});

      ($('input[name="nocache"]')).prop('checked', this.model.get('nocache'));
      ($('.modal-header .close')).remove();
      (this.$el.children(":first")).modal();

      this.model.backup();

      this.model.bind('change', this.render);

      this.render();
      this.validate();
      return false;
    }

    render() {
      this.undelegateEvents();
      for (var f of Array.from('mimetype uri file_upload'.split(' '))) { ($(f)).attr('disabled', true); }
      ($('#modalLabel')).text("Edit Asset");
      ($('.asset-location')).hide(); ($('.uri')).hide(); ($('.skip_asset_check_checkbox')).hide();
      ($('.asset-location.edit')).show();
      ($('.mime-select')).prop('disabled', 'true');

      if ((this.model.get('mimetype')) === 'video') {
        (this.$f('duration')).prop('disabled', true);
      }

      for (var field of Array.from(this.model.fields)) {
        if ((this.$fv(field)) !== this.model.get(field)) {
          this.$fv(field, this.model.get(field));
        }
      }
      ($('.uri-text')).html(insertWbr(truncate_str((this.model.get('uri')))));

      for (var which of ['start', 'end']) {
        var d = date_to(this.model.get(`${which}_date`));
        this.$fv(`${which}_date_date`, d.date());
        (this.$f(`${which}_date_date`)).datepicker({autoclose: true, format: dateSettings.datepickerFormat});
        (this.$f(`${which}_date_date`)).datepicker('setValue', d.date());
        this.$fv(`${which}_date_time`, d.time());
      }

      this.displayAdvanced();
      this.delegateEvents();
      return false;
    }

    viewmodel() {
      for (var which of ['start', 'end']) {
        this.$fv(`${which}_date`, (moment((this.$fv(`${which}_date_date`)) + " " + (this.$fv(`${which}_date_time`)), dateSettings.fullDate)).toDate().toISOString());
      }
      return (() => {
        const result = [];
        for (var field of Array.from(this.model.fields)) {
          if (!(this.$f(field)).prop('disabled')) {
            result.push(this.model.set(field, (this.$fv(field)), {silent:true}));
          }
        }
        return result;
      })();
    }

    changeLoopTimes() {
      const current_date = new Date();
      const end_date = new Date();

      switch ($('#loop_times').val()) {
        case "day":
          this.setLoopDateTime((date_to(current_date)), (date_to(end_date.setDate(current_date.getDate() + 1))));
          break;
        case "week":
          this.setLoopDateTime((date_to(current_date)), (date_to(end_date.setDate(current_date.getDate() + 7))));
          break;
        case "month":
          this.setLoopDateTime((date_to(current_date)), (date_to(end_date.setMonth(current_date.getMonth() + 1))));
          break;
        case "year":
          this.setLoopDateTime((date_to(current_date)), (date_to(end_date.setFullYear(current_date.getFullYear() + 1))));
          break;
        case "forever":
          this.setLoopDateTime((date_to(current_date)), (date_to(end_date.setFullYear(9999))));
          break;
        case "manual":
          this.setDisabledDatepicker(false);
          ($("#manul_date")).show();
          return;
          break;
        default:
          return;
      }
      this.setDisabledDatepicker(true);
      return ($("#manul_date")).hide();
    }

    save(e) {
      this.viewmodel();
      let save = null;
      this.model.set('nocache', ($('input[name="nocache"]')).prop('checked') ? 1 : 0);

      if (!this.model.get('name')) {
        if (this.model.old_name()) {
          this.model.set({name: this.model.old_name()}, {silent:true});
        } else if (getMimetype(this.model.get('uri'))) {
          this.model.set({name: get_filename(this.model.get('uri'))}, {silent:true});
        } else {
          this.model.set({name: this.model.get('uri')}, {silent:true});
        }
      }
      save = this.model.save();

      ($('input, select')).prop('disabled', true);
      save.done(data => {
        this.model.id = data.asset_id;
        if (!this.model.collection) { this.collection.add(this.model); }
        (this.$el.children(":first")).modal('hide');
        return _.extend(this.model.attributes, data);
      });
      save.fail(() => {
        ($('.progress')).hide();
        return ($('input, select')).prop('disabled', false);
      });
      return false;
    }

    change(e) {
      if (!this._change) { this._change = _.throttle((() => {
        this.changeLoopTimes();
        this.viewmodel();
        this.model.trigger('change');
        this.validate(e);
        return true;
      }), 500); }
      return this._change(...arguments);
    }

    validate(e) {
      let field, v;
      const that = this;
      const validators = {
        duration: v => {
          if (('video' !== this.model.get('mimetype')) && (!(_.isNumber(v*1) ) || ((v*1) < 1))) {
            return 'Please enter a valid number.';
          }
        },
        end_date: v => {
          if (!((new Date(this.$fv('start_date'))) < (new Date(this.$fv('end_date'))))) {
            if (__guard__($(e != null ? e.target : undefined), x => x.attr("name")) === "start_date_date") {
              const start_date = new Date(this.$fv('start_date'));
              const end_date = new Date(start_date.getTime() + (Math.max(parseInt(this.$fv('duration')), 60) * 1000));
              this.setLoopDateTime((date_to(start_date)), (date_to(end_date)));
              return;
            }

            return 'End date should be after start date.';
          }
        }
      };
      const errors = ((() => {
        const result = [];
        for (field in validators) {
          var fn = validators[field];
          if ((v = fn((this.$fv(field))))) {
            result.push([field, v]);
          }
        }
        return result;
      })());

      ($(".form-group .help-inline.invalid-feedback")).remove();
      ($(".form-group .form-control")).removeClass('is-invalid');
      ($('[type=submit]')).prop('disabled', false);
      return (() => {
        const result1 = [];
        for ([field, v] of Array.from(errors)) {
          ($('[type=submit]')).prop('disabled', true);
          ($(`.form-group.${field} .form-control`)).addClass('is-invalid');
          result1.push(($(`.form-group.${field} .controls`)).append(
            $((`<span class='help-inline invalid-feedback'>${v}</span>`))));
        }
        return result1;
      })();
    }


    cancel(e) {
      this.model.rollback();
      return (this.$el.children(":first")).modal('hide');
    }

    toggleAdvanced() {
      ($('.fa-play')).toggleClass('rotated');
      ($('.fa-play')).toggleClass('unrotated');
      return ($('.collapse-advanced')).collapse('toggle');
    }

    displayAdvanced() {
      const img = 'image' === this.$fv('mimetype');
      const edit = url_test(this.model.get('uri'));
      const has_nocache = img && edit;
      return ($('.advanced-accordion')).toggle(has_nocache === true);
    }

    setLoopDateTime(start_date, end_date) {
      this.$fv("start_date_date", start_date.date());
      (this.$f("start_date_date")).datepicker({autoclose: true, format: dateSettings.datepickerFormat});
      (this.$f("start_date_date")).datepicker('setDate', moment(start_date.date(), dateSettings.date).toDate());
      this.$fv("start_date_time", start_date.time());
      this.$fv("end_date_date", end_date.date());
      (this.$f("end_date_date")).datepicker({autoclose: true, format: dateSettings.datepickerFormat});
      (this.$f("end_date_date")).datepicker('setDate', moment(end_date.date(), dateSettings.date).toDate());
      this.$fv("end_date_time", end_date.time());

      ($(".form-group .help-inline.invalid-feedback")).remove();
      ($(".form-group .form-control")).removeClass('is-invalid');
      return ($('[type=submit]')).prop('disabled', false);
    }

    setDisabledDatepicker(b) {
      return (() => {
        const result = [];
        for (var which of ['start', 'end']) {
          (this.$f(`${which}_date_date`)).attr('disabled', b);
          result.push((this.$f(`${which}_date_time`)).attr('disabled', b));
        }
        return result;
      })();
    }
  };
  EditAssetView.initClass();
  return EditAssetView;
})());

API.View.AssetRowView = (AssetRowView = (function() {
  AssetRowView = class AssetRowView extends Backbone.View {
    constructor(...args) {
      super(...args);
      this.initialize = this.initialize.bind(this);
      this.render = this.render.bind(this);
      this.toggleIsEnabled = this.toggleIsEnabled.bind(this);
      this.setEnabled = this.setEnabled.bind(this);
      this.download = this.download.bind(this);
      this.edit = this.edit.bind(this);
      this.delete = this.delete.bind(this);
      this.showPopover = this.showPopover.bind(this);
      this.hidePopover = this.hidePopover.bind(this);
    }

    static initClass() {
      this.prototype.tagName = "tr";

      this.prototype.events = {
        'change .is_enabled-toggle input': 'toggleIsEnabled',
        'click .download-asset-button': 'download',
        'click .edit-asset-button': 'edit',
        'click .delete-asset-button': 'showPopover'
      };
    }

    initialize(options) {
      return this.template = get_template('asset-row');
    }

    render() {
      let json;
      this.$el.html(this.template(_.extend((json = this.model.toJSON()), {
        name: insertWbr(truncate_str(json.name)), // word break urls at slashes
        duration: durationSecondsToHumanReadable(json.duration),
        start_date: (date_to(json.start_date)).string(),
        end_date: (date_to(json.end_date)).string()
      }
      )
      )
      );
      this.$el.prop('id', this.model.get('asset_id'));
      ($(".delete-asset-button")).popover({content: get_template('confirm-delete')});
      ($(".toggle input")).prop("checked", this.model.get('is_enabled'));
      ($(".asset-icon")).addClass((() => { switch (this.model.get("mimetype")) {
        case "video":     return "fas fa-video";
        case "streaming": return "fas fa-video";
        case "image":     return "far fa-image";
        case "webpage":   return "fas fa-globe-americas";
        default: return "";

      } })());

      if ((this.model.get("is_processing")) === 1) {
        ($('input, button')).prop('disabled', true);
        ($(".asset-toggle")).html(get_template('processing-message'));
      }

      return this.el;
    }

    toggleIsEnabled(e) {
      const val = (1 + this.model.get('is_enabled')) % 2;
      this.model.set({is_enabled: val});
      this.setEnabled(false);
      const save = this.model.save();
      save.done(() => this.setEnabled(true));
      save.fail(() => {
        this.model.set(this.model.previousAttributes(), {silent:true}); // revert changes
        this.setEnabled(true);
        return this.render();
      });
      return true;
    }

    setEnabled(enabled) { if (enabled) {
      this.$el.removeClass('warning');
      this.delegateEvents();
      return ($('input, button')).prop('disabled', false);
    } else {
      this.hidePopover();
      this.undelegateEvents();
      this.$el.addClass('warning');
      return ($('input, button')).prop('disabled', true);
    } }

    download(e) {
      $.get('/api/v1/assets/' + this.model.id + '/content', function(result) {
        switch (result['type']) {
          case 'url':
            return window.open(result['url']);
          case 'file':
            var content = base64js.toByteArray(result['content']);

            var mimetype = result['mimetype'];
            var fn = result['filename'];

            var blob = new Blob([content], {type: mimetype});
            var url = URL.createObjectURL(blob);

            var a = document.createElement('a');
            document.body.appendChild(a);
            a.download = fn;
            a.href = url;
            a.click();

            URL.revokeObjectURL(url);
            return a.remove();
        }
      });
      return false;
    }

    edit(e) {
      new EditAssetView({model: this.model});
      return false;
    }

    delete(e) {
      let xhr;
      this.hidePopover();
      if ((xhr = this.model.destroy()) === !false) {
        xhr.done(() => this.remove());
      } else {
        this.remove();
      }
      return false;
    }

    showPopover() {
      if (!($('.popover')).length) {
        ($(".delete-asset-button")).popover('show');
        ($('.confirm-delete')).click(this.delete);
        ($(window)).one('click', this.hidePopover);
      }
      return false;
    }

    hidePopover() {
      ($(".delete-asset-button")).popover('hide');
      return false;
    }
  };
  AssetRowView.initClass();
  return AssetRowView;
})());


API.View.AssetsView = (AssetsView = class AssetsView extends Backbone.View {
  // constructor(...args) {
  //   super(...args);
  //   this.initialize = this.initialize.bind(this);
  //   this.update_order = this.update_order.bind(this);
  //   this.render = this.render.bind(this);
  // }

  constructor(...args) {
    super(...args);
    for (var event of Array.from(('reset add remove sync'.split(' ')))) { this.collection.bind(event, this.render); }
    return this.sorted = ($('#active-assets')).sortable({
      containment: 'parent',
      axis: 'y',
      helper: 'clone',
      update: this.update_order
    });
  }

  update_order() {
    let id;
    const active = ($('#active-assets')).sortable('toArray');

    for (let i = 0; i < active.length; i++) { id = active[i]; this.collection.get(id).set('play_order', i); }
    for (var el of Array.from(($('#inactive-assets tr')).toArray())) { this.collection.get(el.id).set('play_order', active.length); }

    return $.post('/api/v1/assets/order', {ids: (($('#active-assets')).sortable('toArray')).join(',')});
  }

  render(options) {
    console.log(this);
    let which;
    this.collection = options.collection;
    this.collection.sort();

    for (which of ['active', 'inactive']) { ($(`#${which}-assets`)).html(''); }

    this.collection.each(model => {
      which = model.active() ? 'active' : 'inactive';
      return ($(`#${which}-assets`)).append((new AssetRowView({model})).render());
    });

    for (which of ['active', 'inactive']) {
      if (($(`#${which}-assets tr`)).length === 0) {
        ($(`#${which}-assets-section .table-assets-help-text`)).show();
      } else {
        ($(`#${which}-assets-section .table-assets-help-text`)).hide();
      }
    }

    for (which of ['inactive', 'active']) {
      $(`.${which}-table thead`).toggle(!!($(`#${which}-assets tr`).length));
    }

    this.update_order();

    return this.el;
  }
});


API.App = (App = (function() {
  App = class App extends Backbone.View {
    constructor(...args) {
      super(...args);
      this.initialize = this.initialize.bind(this);
    }

    static initClass() {

      this.prototype.events = {
        'click .add-asset-button': 'add',
        'click #previous-asset-button': 'previous',
        'click #next-asset-button': 'next'
      };
    }
    initialize() {
      ($(window)).ajaxError(function(e,r) {
        let err, j;
        ($('#request-error')).html((get_template('request-error'))());
        if ((j = $.parseJSON(r.responseText)) && (err = j.error)) {
          ($('#request-error .msg')).text('Server Error: ' + err);
        }
        ($('#request-error')).show();
        return setTimeout(() => ($('#request-error')).fadeOut('slow')
        , 5000);
      });
      ($(window)).ajaxSuccess(function(event, request, settings) {
        if ((settings.url === new Assets().url) && (settings.type === 'POST')) {
          ($('#request-error')).html((get_template('request-success'))());
          ($('#request-error .msg')).text('Asset has been successfully uploaded.');
          ($('#request-error')).show();
          return setTimeout(() => ($('#request-error')).fadeOut('slow')
          , 5000);
        }
      });

      (API.assets = new Assets()).fetch();
      API.assetsView = new AssetsView({
        collection: API.assets,
        el: $('#assets')
      });

      return Array.from(wsAddresses).map((address) =>
        (() => { try {
          const ws = new WebSocket(address);
          return ws.onmessage = x => x.data.text().then(function(assetId) {
            const model = API.assets.get(assetId);
            if (model) {
              let save;
              return save = model.fetch();
            }
          });
        } catch (error) {
          return false;
        } })());
    }

    add(e) {
      new AddAssetView;
      return false;
    }

    previous(e) {
      return $.get('/api/v1/assets/control/previous');
    }

    next(e) {
      return $.get('/api/v1/assets/control/next');
    }
  };
  App.initClass();
  return App;
})());

function __guard__(value, transform) {
  return (typeof value !== 'undefined' && value !== null) ? transform(value) : undefined;
}
