import { RootState } from '@/types'

export const ToggleableSetting = ({
  settings,
  handleInputChange,
  label,
  name,
}: {
  settings: RootState['settings']['settings']
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  label: string
  name: string
}) => {
  return (
    <div className="d-flex align-items-center mt-4">
      <label>{label}</label>
      <div className="ms-auto">
        <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
          <input
            name={name}
            type="checkbox"
            checked={
              settings[
                name as keyof RootState['settings']['settings']
              ] as boolean
            }
            onChange={handleInputChange}
          />
          <span>
            <span></span>
            <span></span>
            <a></a>
          </span>
        </label>
      </div>
    </div>
  )
}
