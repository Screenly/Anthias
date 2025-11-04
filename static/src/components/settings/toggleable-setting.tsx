import classNames from 'classnames'

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
    <div className={classNames('d-flex', 'align-items-center', 'mt-4')}>
      <label htmlFor={name}>{label}</label>
      <div className={classNames('form-check', 'form-switch', 'ms-auto')}>
        <input
          className={classNames('form-check-input', 'shadow-none')}
          type="checkbox"
          role="switch"
          id={name}
          name={name}
          checked={
            settings[name as keyof RootState['settings']['settings']] as boolean
          }
          onChange={handleInputChange}
        />
      </div>
    </div>
  )
}
