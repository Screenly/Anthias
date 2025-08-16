import { RootState } from '@/types'

export const PlayerName = ({
  settings,
  handleInputChange,
}: {
  settings: RootState['settings']['settings']
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void
}) => {
  return (
    <div className="mb-3">
      <label className="small text-secondary">
        <small>Player name</small>
      </label>
      <input
        className="form-control shadow-none"
        name="playerName"
        type="text"
        value={settings.playerName}
        onChange={handleInputChange}
      />
    </div>
  )
}
