import {
  AppDispatch,
  AssetEditData,
  EditFormData,
  HandleSubmitParams,
} from '@/types'

interface ModalFooterProps {
  asset: AssetEditData | null
  formData: EditFormData
  startDateDate: string
  startDateTime: string
  endDateDate: string
  endDateTime: string
  dispatch: AppDispatch
  onClose: () => void
  handleClose: () => void
  isSubmitting: boolean
  handleSubmit: (params: HandleSubmitParams) => void
  setIsSubmitting: (isSubmitting: boolean) => void
}

export const ModalFooter = ({
  asset,
  formData,
  startDateDate,
  startDateTime,
  endDateDate,
  endDateTime,
  dispatch,
  onClose,
  handleClose,
  isSubmitting,
  handleSubmit,
  setIsSubmitting,
}: ModalFooterProps) => {
  return (
    <div className="modal-footer">
      <div className="float-start progress active" style={{ display: 'none' }}>
        <div className="bar progress-bar-striped progress-bar progress-bar-animated"></div>
      </div>
      <button
        className="btn btn-info btn-long cancel"
        type="button"
        onClick={handleClose}
        disabled={isSubmitting}
      >
        Cancel
      </button>
      <button
        id="save-asset"
        className="btn btn-primary btn-long"
        type="submit"
        onClick={(e) =>
          asset &&
          handleSubmit({
            e,
            asset,
            formData,
            startDateDate,
            startDateTime,
            endDateDate,
            endDateTime,
            dispatch,
            onClose,
            setIsSubmitting,
          })
        }
        disabled={isSubmitting}
      >
        Save
      </button>
    </div>
  )
}
