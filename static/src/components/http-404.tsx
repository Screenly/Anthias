import React from 'react';
import classNames from 'classnames';

const Http404: React.FC = () => {
  return (
    <div
      className={classNames(
        'container',
        'd-flex',
        'align-items-center',
        'justify-content-center',
        'pt-5',
        'mb-5',
        'bg-dark',
        'text-primary',
      )}
    >
      <div className="col-12 d-table-cell align-middle">
        <div className="p-5">
          <div className="row">
            <div className="col-12 text-center">
              <div className="mb-2">
                <h1 className="display-1">404</h1>
              </div>
              <h3 className="mb-5">
                <b>Page Not Found</b>
              </h3>
              <p className="mb-4 text-white">
                The page you are looking for might have been removed,
                <br />
                had its name changed, or is temporarily unavailable.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Http404;
