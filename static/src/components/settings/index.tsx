import { useEffect } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import Swal from 'sweetalert2';
import { RootState, AppDispatch } from '@/types';

import { SWEETALERT_TIMER } from '@/constants';
import {
  fetchSettings,
  fetchDeviceModel,
  updateSettings,
  updateSetting,
} from '@/store/settings';
import { SystemControls } from '@/components/settings/system-controls';
import { Backup } from '@/components/settings/backup';
import { Authentication } from '@/components/settings/authentication';
import { PlayerName } from '@/components/settings/player-name';
import { DefaultDurations } from '@/components/settings/default-durations';
import { AudioOutput } from '@/components/settings/audio-output';
import { DateFormat } from '@/components/settings/date-format';
import { ToggleableSetting } from '@/components/settings/toggleable-setting';

export const Settings = () => {
  const dispatch = useDispatch<AppDispatch>();
  const { settings, deviceModel, isLoading } = useSelector(
    (state: RootState) => state.settings,
  );

  useEffect(() => {
    document.title = 'Settings';
    dispatch(fetchSettings());
    dispatch(fetchDeviceModel());
  }, [dispatch]);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value, type, checked } = e.target;
    dispatch(
      updateSetting({ name, value: type === 'checkbox' ? checked : value }),
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    try {
      await dispatch(updateSettings(settings)).unwrap();

      await Swal.fire({
        title: 'Success!',
        text: 'Settings were successfully saved.',
        icon: 'success',
        timer: SWEETALERT_TIMER,
        showConfirmButton: false,
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
        },
      });

      dispatch(fetchSettings());
    } catch (err) {
      await Swal.fire({
        title: 'Error!',
        text: (err as Error).message || 'Failed to save settings',
        icon: 'error',
        customClass: {
          popup: 'swal2-popup',
          title: 'swal2-title',
          htmlContainer: 'swal2-html-container',
          confirmButton: 'swal2-confirm',
        },
      });
    }
  };

  return (
    <div className="container">
      <div className="row py-2">
        <div className="col-12">
          <h4 className="page-header text-white">
            <b>Settings</b>
          </h4>
        </div>
      </div>

      <div className="row content px-3">
        <div className="col-12 my-3">
          <form onSubmit={handleSubmit} className="row">
            <div className="form-group col-6 d-flex flex-column justify-content-between">
              <PlayerName
                settings={settings}
                handleInputChange={handleInputChange}
              />

              <DefaultDurations
                settings={settings}
                handleInputChange={handleInputChange}
              />

              <AudioOutput
                settings={settings}
                handleInputChange={handleInputChange}
                deviceModel={deviceModel}
              />

              <DateFormat
                settings={settings}
                handleInputChange={handleInputChange}
              />

              <Authentication />
            </div>

            <div className="form-group col-6 d-flex flex-column justify-content-start">
              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Show splash screen"
                name="showSplash"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Default assets"
                name="defaultAssets"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Shuffle playlist"
                name="shufflePlaylist"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Use 24-hour clock"
                name="use24HourClock"
              />

              <ToggleableSetting
                settings={settings}
                handleInputChange={handleInputChange}
                label="Debug logging"
                name="debugLogging"
              />
            </div>

            <div className="form-group col-12">
              <div className="text-right">
                <a className="btn btn-long btn-outline-primary mr-2" href="/">
                  Cancel
                </a>
                <button
                  className="btn btn-long btn-primary"
                  type="submit"
                  disabled={isLoading}
                >
                  {isLoading ? (
                    <span
                      className="spinner-border spinner-border-sm"
                      role="status"
                      aria-hidden="true"
                    ></span>
                  ) : (
                    'Save Settings'
                  )}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>

      <Backup />
      <SystemControls />
    </div>
  );
};
