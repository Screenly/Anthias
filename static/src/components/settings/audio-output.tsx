import { RootState } from '@/types'

export const AudioOutput = ({
  settings,
  handleInputChange,
  deviceModel,
}: {
  settings: RootState['settings']['settings']
  handleInputChange: (e: React.ChangeEvent<HTMLSelectElement>) => void
  deviceModel: string
}) => {
  return (
    <div className="form-group">
      <label className="small text-secondary">
        <small>Audio output</small>
      </label>
      <select
        className="form-control shadow-none"
        name="audioOutput"
        value={settings.audioOutput}
        onChange={handleInputChange}
      >
        <option value="hdmi">HDMI</option>
        {!deviceModel.includes('Raspberry Pi 5') && (
          <option value="local">3.5mm jack</option>
        )}
      </select>
    </div>
  )
}
