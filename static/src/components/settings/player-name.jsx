export const PlayerName = ({ settings, handleInputChange }) => {
  return (
    <div className="form-group">
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
