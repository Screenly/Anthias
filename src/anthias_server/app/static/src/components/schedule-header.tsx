import { PlayerNameBadge } from '@/components/player-name-badge'
import { ScheduleActionButtons } from '@/components/schedule-action-buttons'

interface ScheduleHeaderProps {
  playerName: string
  onPreviousAsset: (event: React.MouseEvent) => void
  onNextAsset: (event: React.MouseEvent) => void
  onAddAsset: (event: React.MouseEvent) => void
}

export const ScheduleHeader = ({
  playerName,
  onPreviousAsset,
  onNextAsset,
  onAddAsset,
}: ScheduleHeaderProps) => {
  return (
    <div className="container pt-3 pb-3">
      <div className="row">
        <div className="col-12">
          <h4 className="mb-3">
            <b className="text-white">Schedule Overview</b>
          </h4>

          <PlayerNameBadge playerName={playerName} />

          <ScheduleActionButtons
            onPreviousAsset={onPreviousAsset}
            onNextAsset={onNextAsset}
            onAddAsset={onAddAsset}
          />
        </div>
      </div>
    </div>
  )
}
