import { RootState } from '@/types';

export const DefaultDurations = ({
  settings,
  handleInputChange,
}: {
  settings: RootState['settings']['settings'];
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}) => {
  return (
    <div className="row">
      <div className="form-group col-6">
        <label className="small text-secondary">
          <small>Default duration (seconds)</small>
        </label>
        <input
          className="form-control shadow-none"
          name="defaultDuration"
          type="number"
          value={settings.defaultDuration}
          onChange={handleInputChange}
        />
      </div>
      <div className="form-group col-6">
        <label className="small text-secondary">
          <small>Default streaming duration (seconds)</small>
        </label>
        <input
          className="form-control shadow-none"
          name="defaultStreamingDuration"
          type="number"
          value={settings.defaultStreamingDuration}
          onChange={handleInputChange}
        />
      </div>
    </div>
  );
};
