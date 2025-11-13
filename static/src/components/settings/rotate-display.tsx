import { RootState } from '@/types'

export const RotateDisplay = ({
  settings,
  handleInputChange,
}: {
  settings: RootState['settings']['settings']
  handleInputChange: (e: React.ChangeEvent<HTMLSelectElement>) => void
}) => {
  return (
    <div className="mb-3">
      <label className="small text-secondary">
        <small>Rotate Display</small>
      </label>
      <select
        className="form-control shadow-none form-select"
        name="rotateDisplay"
        value={settings.rotateDisplay || '0'}
        onChange={handleInputChange}
      >
        <option value={0}>0&deg;</option>
        <option value={90}>90&deg;</option>
        <option value={180}>180&deg;</option>
        <option value={270}>270&deg;</option>
      </select>
    </div>
  )
}
