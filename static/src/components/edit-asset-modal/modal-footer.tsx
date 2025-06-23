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
}) => {
  return (
    <div className="modal-footer">
      <div className="float-left progress active" style={{ display: 'none' }}>
        <div className="bar progress-bar-striped progress-bar progress-bar-animated"></div>
      </div>
      <button
        className="btn btn-outline-primary btn-long cancel"
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
  );
};
