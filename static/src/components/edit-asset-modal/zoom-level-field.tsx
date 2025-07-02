import { EditFormData } from '@/types';

interface ZoomLevelFieldProps {
  formData: EditFormData;
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}

export const ZoomLevelField = ({
  formData,
  handleInputChange,
}: ZoomLevelFieldProps) => {
  return (
    <div className="form-group row duration">
      <label className="col-4 col-form-label">Zoom Level</label>
      <div className="col-7 controls">
        <input
          className="form-control shadow-none"
          name="zoom_level"
          type="number"
          value={formData.zoom_level}
          min={0.25}
          max={5.0}
          step={0.25}
          onChange={handleInputChange}
          disabled={formData.mimetype === 'video'}
        />
      </div>
    </div>
  );
};
