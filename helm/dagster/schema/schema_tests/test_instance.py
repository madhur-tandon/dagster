import pytest
import yaml
from dagster.core.run_coordinator import QueuedRunCoordinator
from dagster_aws.s3.compute_log_manager import S3ComputeLogManager
from dagster_azure.blob.compute_log_manager import AzureBlobComputeLogManager
from dagster_gcp.gcs.compute_log_manager import GCSComputeLogManager
from kubernetes.client import models
from schema.charts.dagster.subschema.compute_log_manager import (
    AzureBlobComputeLogManager as AzureBlobComputeLogManagerModel,
)
from schema.charts.dagster.subschema.compute_log_manager import (
    ComputeLogManager,
    ComputeLogManagerConfig,
    ComputeLogManagerType,
)
from schema.charts.dagster.subschema.compute_log_manager import (
    GCSComputeLogManager as GCSComputeLogManagerModel,
)
from schema.charts.dagster.subschema.compute_log_manager import (
    S3ComputeLogManager as S3ComputeLogManagerModel,
)
from schema.charts.dagster.subschema.daemon import (
    ConfigurableClass,
    Daemon,
    QueuedRunCoordinatorConfig,
    RunCoordinator,
    RunCoordinatorConfig,
    RunCoordinatorType,
    TagConcurrencyLimit,
)
from schema.charts.dagster.subschema.postgresql import PostgreSQL, Service
from schema.charts.dagster.subschema.run_launcher import (
    K8sRunLauncherConfig,
    RunLauncher,
    RunLauncherConfig,
    RunLauncherType,
)
from schema.charts.dagster.values import DagsterHelmValues
from schema.utils.helm_template import HelmTemplate


def to_camel_case(s: str) -> str:
    components = s.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


@pytest.fixture(name="template")
def helm_template() -> HelmTemplate:
    return HelmTemplate(
        helm_dir_path="helm/dagster",
        subchart_paths=["charts/dagster-user-deployments"],
        output="templates/configmap-instance.yaml",
        model=models.V1ConfigMap,
    )


@pytest.mark.parametrize("storage", ["schedule_storage", "run_storage", "event_log_storage"])
def test_storage_postgres_db_config(template: HelmTemplate, storage: str):
    postgresql_username = "username"
    postgresql_host = "1.1.1.1"
    postgresql_database = "database"
    postgresql_params = {
        "connect_timeout": 10,
        "application_name": "myapp",
        "options": "-c synchronous_commit=off",
    }
    postgresql_port = 8080
    helm_values = DagsterHelmValues.construct(
        postgresql=PostgreSQL.construct(
            postgresqlUsername=postgresql_username,
            postgresqlHost=postgresql_host,
            postgresqlDatabase=postgresql_database,
            postgresqlParams=postgresql_params,
            service=Service(port=postgresql_port),
        )
    )

    configmaps = template.render(helm_values)

    assert len(configmaps) == 1

    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])

    assert instance[storage]

    postgres_db = instance[storage]["config"]["postgres_db"]

    assert postgres_db["username"] == postgresql_username
    assert postgres_db["password"] == {"env": "DAGSTER_PG_PASSWORD"}
    assert postgres_db["hostname"] == postgresql_host
    assert postgres_db["db_name"] == postgresql_database
    assert postgres_db["port"] == postgresql_port
    assert postgres_db["params"] == postgresql_params


def test_k8s_run_launcher_config(template: HelmTemplate):
    job_namespace = "namespace"
    load_incluster_config = True
    env_config_maps = [{"name": "env_config_map"}]
    env_secrets = [{"name": "secret"}]
    env_vars = ["ENV_VAR"]
    helm_values = DagsterHelmValues.construct(
        runLauncher=RunLauncher.construct(
            type=RunLauncherType.K8S,
            config=RunLauncherConfig.construct(
                k8sRunLauncher=K8sRunLauncherConfig.construct(
                    jobNamespace=job_namespace,
                    loadInclusterConfig=load_incluster_config,
                    envConfigMaps=env_config_maps,
                    envSecrets=env_secrets,
                    envVars=env_vars,
                )
            ),
        )
    )

    configmaps = template.render(helm_values)
    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])
    run_launcher_config = instance["run_launcher"]

    assert run_launcher_config["module"] == "dagster_k8s"
    assert run_launcher_config["class"] == "K8sRunLauncher"
    assert run_launcher_config["config"]["job_namespace"] == job_namespace
    assert run_launcher_config["config"]["load_incluster_config"] == load_incluster_config
    assert run_launcher_config["config"]["env_config_maps"][1:] == [
        configmap["name"] for configmap in env_config_maps
    ]
    assert run_launcher_config["config"]["env_secrets"] == [
        secret["name"] for secret in env_secrets
    ]
    assert run_launcher_config["config"]["env_vars"] == env_vars


@pytest.mark.parametrize("enabled", [True, False])
def test_queued_run_coordinator_config(template: HelmTemplate, enabled: bool):
    max_concurrent_runs = 50
    tag_concurrency_limits = [TagConcurrencyLimit(key="key", value="value", limit=10)]
    dequeue_interval_seconds = 50
    helm_values = DagsterHelmValues.construct(
        dagsterDaemon=Daemon.construct(
            runCoordinator=RunCoordinator.construct(
                enabled=enabled,
                type=RunCoordinatorType.QUEUED,
                config=RunCoordinatorConfig.construct(
                    queuedRunCoordinator=QueuedRunCoordinatorConfig.construct(
                        maxConcurrentRuns=max_concurrent_runs,
                        tagConcurrencyLimits=tag_concurrency_limits,
                        dequeueIntervalSeconds=dequeue_interval_seconds,
                    )
                ),
            )
        )
    )
    configmaps = template.render(helm_values)
    assert len(configmaps) == 1

    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])

    assert ("run_coordinator" in instance) == enabled
    if enabled:
        assert instance["run_coordinator"]["module"] == "dagster.core.run_coordinator"
        assert instance["run_coordinator"]["class"] == "QueuedRunCoordinator"
        assert instance["run_coordinator"]["config"]

        run_coordinator_config = instance["run_coordinator"]["config"]

        assert run_coordinator_config["max_concurrent_runs"] == max_concurrent_runs
        assert run_coordinator_config["dequeue_interval_seconds"] == dequeue_interval_seconds

        assert len(run_coordinator_config["tag_concurrency_limits"]) == len(tag_concurrency_limits)
        assert run_coordinator_config["tag_concurrency_limits"] == [
            tag_concurrency_limit.dict() for tag_concurrency_limit in tag_concurrency_limits
        ]


def test_custom_run_coordinator_config(template: HelmTemplate):
    module = "a_module"
    class_ = "Class"
    config_field_one = "1"
    config_field_two = "two"
    config = {"config_field_one": config_field_one, "config_field_two": config_field_two}
    helm_values = DagsterHelmValues.construct(
        dagsterDaemon=Daemon.construct(
            runCoordinator=RunCoordinator.construct(
                enabled=True,
                type=RunCoordinatorType.CUSTOM,
                config=RunCoordinatorConfig.construct(
                    customRunCoordinator=ConfigurableClass.construct(
                        module=module,
                        class_=class_,
                        config=config,
                    )
                ),
            )
        )
    )
    configmaps = template.render(helm_values)
    assert len(configmaps) == 1

    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])

    assert instance["run_coordinator"]["module"] == module
    assert instance["run_coordinator"]["class"] == class_
    assert instance["run_coordinator"]["config"] == config


@pytest.mark.parametrize(
    "compute_log_manager_type",
    [ComputeLogManagerType.NOOP, ComputeLogManagerType.LOCAL],
    ids=["noop", "local compute log manager becomes noop"],
)
def test_noop_compute_log_manager(
    template: HelmTemplate, compute_log_manager_type: ComputeLogManagerType
):
    helm_values = DagsterHelmValues.construct(
        computeLogManager=ComputeLogManager.construct(type=compute_log_manager_type)
    )

    configmaps = template.render(helm_values)
    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])
    compute_logs_config = instance["compute_logs"]

    assert compute_logs_config["module"] == "dagster.core.storage.noop_compute_log_manager"
    assert compute_logs_config["class"] == "NoOpComputeLogManager"


def test_azure_blob_compute_log_manager(template: HelmTemplate):
    storage_account = "account"
    container = "container"
    secret_key = "secret_key"
    local_dir = "/dir"
    prefix = "prefix"
    helm_values = DagsterHelmValues.construct(
        computeLogManager=ComputeLogManager.construct(
            type=ComputeLogManagerType.AZURE,
            config=ComputeLogManagerConfig.construct(
                azureBlobComputeLogManager=AzureBlobComputeLogManagerModel(
                    storageAccount=storage_account,
                    container=container,
                    secretKey=secret_key,
                    localDir=local_dir,
                    prefix=prefix,
                )
            ),
        )
    )

    configmaps = template.render(helm_values)
    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])
    compute_logs_config = instance["compute_logs"]

    assert compute_logs_config["module"] == "dagster_azure.blob.compute_log_manager"
    assert compute_logs_config["class"] == "AzureBlobComputeLogManager"
    assert compute_logs_config["config"] == {
        "storage_account": storage_account,
        "container": container,
        "secret_key": secret_key,
        "local_dir": local_dir,
        "prefix": prefix,
    }

    # Test all config fields in configurable class
    assert compute_logs_config["config"].keys() == AzureBlobComputeLogManager.config_type().keys()


def test_gcs_compute_log_manager(template: HelmTemplate):
    bucket = "bucket"
    local_dir = "/dir"
    prefix = "prefix"
    helm_values = DagsterHelmValues.construct(
        computeLogManager=ComputeLogManager.construct(
            type=ComputeLogManagerType.GCS,
            config=ComputeLogManagerConfig.construct(
                gcsComputeLogManager=GCSComputeLogManagerModel(
                    bucket=bucket, localDir=local_dir, prefix=prefix
                )
            ),
        )
    )

    configmaps = template.render(helm_values)
    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])
    compute_logs_config = instance["compute_logs"]

    assert compute_logs_config["module"] == "dagster_gcp.gcs.compute_log_manager"
    assert compute_logs_config["class"] == "GCSComputeLogManager"
    assert compute_logs_config["config"] == {
        "bucket": bucket,
        "local_dir": local_dir,
        "prefix": prefix,
    }

    # Test all config fields in configurable class
    assert compute_logs_config["config"].keys() == GCSComputeLogManager.config_type().keys()


def test_s3_compute_log_manager(template: HelmTemplate):
    bucket = "bucket"
    local_dir = "/dir"
    prefix = "prefix"
    use_ssl = True
    verify = True
    verify_cert_path = "/path"
    endpoint_url = "endpoint.com"
    skip_empty_files = True
    helm_values = DagsterHelmValues.construct(
        computeLogManager=ComputeLogManager.construct(
            type=ComputeLogManagerType.S3,
            config=ComputeLogManagerConfig.construct(
                s3ComputeLogManager=S3ComputeLogManagerModel(
                    bucket=bucket,
                    localDir=local_dir,
                    prefix=prefix,
                    useSsl=use_ssl,
                    verify=verify,
                    verifyCertPath=verify_cert_path,
                    endpointUrl=endpoint_url,
                    skipEmptyFiles=skip_empty_files,
                )
            ),
        )
    )

    configmaps = template.render(helm_values)
    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])
    compute_logs_config = instance["compute_logs"]

    assert compute_logs_config["module"] == "dagster_aws.s3.compute_log_manager"
    assert compute_logs_config["class"] == "S3ComputeLogManager"
    assert compute_logs_config["config"] == {
        "bucket": bucket,
        "local_dir": local_dir,
        "prefix": prefix,
        "use_ssl": use_ssl,
        "verify": verify,
        "verify_cert_path": verify_cert_path,
        "endpoint_url": endpoint_url,
        "skip_empty_files": skip_empty_files,
    }

    # Test all config fields in configurable class
    assert compute_logs_config["config"].keys() == S3ComputeLogManager.config_type().keys()


def test_custom_compute_log_manager_config(template: HelmTemplate):
    module = "a_module"
    class_ = "Class"
    config_field_one = "1"
    config_field_two = "two"
    config = {"config_field_one": config_field_one, "config_field_two": config_field_two}
    helm_values = DagsterHelmValues.construct(
        computeLogManager=ComputeLogManager.construct(
            type=ComputeLogManagerType.CUSTOM,
            config=ComputeLogManagerConfig.construct(
                customComputeLogManager=ConfigurableClass.construct(
                    module=module,
                    class_=class_,
                    config=config,
                )
            ),
        )
    )

    configmaps = template.render(helm_values)
    instance = yaml.full_load(configmaps[0].data["dagster.yaml"])
    compute_logs_config = instance["compute_logs"]

    assert compute_logs_config["module"] == module
    assert compute_logs_config["class"] == class_
    assert compute_logs_config["config"] == config


@pytest.mark.parametrize(
    argnames=["json_schema_model", "compute_log_manager_class"],
    argvalues=[
        (AzureBlobComputeLogManagerModel, AzureBlobComputeLogManager),
        (GCSComputeLogManagerModel, GCSComputeLogManager),
        (S3ComputeLogManagerModel, S3ComputeLogManager),
    ],
)
def test_compute_log_manager_has_schema(json_schema_model, compute_log_manager_class):
    json_schema_fields = json_schema_model.schema()["properties"].keys()
    compute_log_manager_fields = set(
        map(to_camel_case, compute_log_manager_class.config_type().keys())
    )

    assert json_schema_fields == compute_log_manager_fields


@pytest.mark.parametrize(
    argnames=["json_schema_model", "run_coordinator_class"],
    argvalues=[
        (QueuedRunCoordinatorConfig, QueuedRunCoordinator),
    ],
)
def test_run_coordinator_has_schema(json_schema_model, run_coordinator_class):
    json_schema_fields = json_schema_model.schema()["properties"].keys()
    run_coordinator_fields = set(map(to_camel_case, run_coordinator_class.config_type().keys()))

    assert json_schema_fields == run_coordinator_fields
