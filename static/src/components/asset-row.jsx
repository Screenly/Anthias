import { FaGripVertical } from 'react-icons/fa';

export const AssetRow = (props) => {
  return (
    <tr>
      <td className="asset_row_name">
        <FaGripVertical className="mr-2" />
        <i className="asset-icon mr-2"></i>
        { props.name }
      </td>
      <td style={{ width: '21%' }}>
        { props.startDate }
      </td>
      <td style={{ width: '21%' }}>
        { props.endDate }
      </td>
      <td style={{ width: '13%' }}>
        { props.duration }
      </td>
      <td className="asset-toggle" style={{ width: '7%' }}>
        <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
          <input type="checkbox"/>
          <span>
            <span className="off"></span>
            <span className="on"></span>
            <a></a>
          </span>
        </label>
      </td>
      <td className="asset_row_btns">
        <button className="download-asset-button btn btn-outline-dark" type="button">
          <i className="fas fa-download"></i>
        </button>
        <button className="edit-asset-button btn btn-outline-dark" type="button">
          <i className="fas fa-pencil-alt"></i>
        </button>
        <button className="delete-asset-button btn btn-outline-dark" data-html="true" data-placement="left"
            data-title="Are you sure?" data-trigger="manual" type="button">
          <i className="far fa-trash-alt"></i>
        </button>
      </td>
    </tr>
  )
}
