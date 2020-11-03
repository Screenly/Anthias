This is a living document. It will be updated periodically. This is currently work-in-progress, and needs to be linked up with relevant issues.

# Near future

* Make the [new browser](https://github.com/Screenly/screenly-ose-webview) the default browser (i.e. merge in the 'experimental branch').  - [Pull Request](https://github.com/Screenly/screenly-ose/pull/1239)
* Finalize the migration to Docker such that all workloads run in Docker.
* Patch up the new browser to support modern workloads (if necessary)
* Upgrade base image in [pi-gen](https://github.com/Screenly/pi-gen) to use Buster - [Pull Request](https://github.com/Screenly/pi-gen/pull/25)
  * This will enable proper support for Pi 4

# Medium-term

* Migrate to Django and Python 3
  * It would be a good opportunity to begin the migration to Django while doing the migration to Python 3 anyways to avoid two rewrites
* Migrate API to [Django Rest Framework](https://www.django-rest-framework.org/)

# Long-term

* Upgrade [new browser](https://github.com/Screenly/screenly-ose-webview) and add support for debugging
* Add 4k support
