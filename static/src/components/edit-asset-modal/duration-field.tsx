import { EditFormData } from '@/types'

interface DurationFieldProps {
  formData: EditFormData
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void
}

export const DurationField = ({
  formData,
  handleInputChange,
}: DurationFieldProps) => {
  return (
    <div className="row mb-3 duration">
      <label className="col-4 col-form-label">Duration</label>
      <div className="col-8 controls d-flex align-items-center gap-2">
        <input
          className="form-control shadow-none"
          name="duration"
          type="number"
          value={formData.duration}
          onChange={handleInputChange}
          disabled={formData.mimetype === 'video'}
        />
        <span>seconds</span>
      </div>
    </div>
  )
}
