interface PlayerNameBadgeProps {
  playerName: string
}

export const PlayerNameBadge = ({ playerName }: PlayerNameBadgeProps) => {
  if (!playerName) {
    return null
  }

  return (
    <span className="badge bg-primary px-3 py-2 rounded-pill mb-3">
      <h6 className="my-0 text-center text-dark fw-bold">{playerName}</h6>
    </span>
  )
}
