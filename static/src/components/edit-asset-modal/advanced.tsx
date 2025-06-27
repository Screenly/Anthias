import { EditFormData } from '@/types';

interface AdvancedFieldsProps {
  formData: EditFormData;
  handleInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}

export const AdvancedFields = ({
  formData,
  handleInputChange,
}: AdvancedFieldsProps) => {
  return (
    <div className="advanced-accordion accordion">
      <div className="accordion-group">
        <div className="accordion-heading">
          <i className="fas fa-play unrotated"></i>
          <a className="advanced-toggle" href="#">
            Advanced
          </a>
        </div>
        <div className="collapse-advanced accordion-body collapse">
          <div className="accordion-inner">
            <div className="form-group row">
              <label className="col-4 col-form-label">Disable cache</label>
              <div className="col-8 nocache controls justify-content-center align-self-center">
                <label className="nocache-toggle toggle switch-light switch-ios small m-0">
                  <input
                    type="checkbox"
                    name="nocache"
                    checked={formData.nocache}
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
          </div>
        </div>
      </div>
    </div>
  );
};
