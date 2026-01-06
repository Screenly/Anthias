interface PlayerNameBadgeProps {
  playerName: string
}

export const PlayerNameBadge = ({ playerName }: PlayerNameBadgeProps) => {
  if (!playerName) {
    return null
  }

  return (
    <span 
      className="badge px-3 py-2 rounded-pill mb-3"
      style={{ backgroundColor: '#FE8E9F', color: '#4C042D' }}
    >
      <h6 className="my-0 text-center fw-bold">{playerName}</h6>
    </span>
  )
}
