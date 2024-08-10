import json
import logging
import os
from pathlib import Path

import boa
from eth_utils import keccak

from settings.config import BASE_DIR, ChainConfig

from .constants import CREATE2_SALT, CREATE2DEPLOYER_ABI, CREATE2DEPLOYER_ADDRESS
from .deployment_file import YamlDeploymentFile
from .utils import fetch_latest_contract, get_relative_path, get_version_from_filename, version_a_gt_version_b

logger = logging.getLogger(__name__)


def dump_initial_chain_settings(chain_settings: ChainConfig):
    deployment_file_path = Path(BASE_DIR, "deployments", f"{chain_settings.network_name}.yaml")
    deployment_file = YamlDeploymentFile(deployment_file_path)

    deployment_file.dump_initial_chain_settings(chain_settings)


def update_deployment_chain_config(chain_settings: ChainConfig, data: dict):
    deployment_file_path = Path(BASE_DIR, "deployments", f"{chain_settings.network_name}.yaml")
    deployment_file = YamlDeploymentFile(deployment_file_path)

    deployment_file.update_deployment_config({"config": data})


def deploy_contract(chain_settings: ChainConfig, contract_folder: Path, *args, as_blueprint: bool = False):
    deployment_file_path = Path(BASE_DIR, "deployments", f"{chain_settings.network_name}.yaml")
    deployment_file = YamlDeploymentFile(deployment_file_path)

    # fetch latest contract
    latest_contract = fetch_latest_contract(contract_folder)
    version_latest_contract = get_version_from_filename(latest_contract)

    # check if it has been deployed already
    parts = contract_folder.parts
    yaml_keys = contract_folder.parts[parts.index("contracts") :]
    contract_designation = parts[-1]
    # deployed_contract_dict = get_deployment(yaml_keys, deployment_file)
    deployed_contract = deployment_file.get_contract_deployment(yaml_keys)

    # if deployed, fetch deployed version
    deployed_contract_version = "0.0.0"  # contract has never been deployed
    if deployed_contract:
        deployed_contract_version = deployed_contract.contract_version  # contract has been deployed

    # deploy contract if nothing has been deployed, or if deployed contract is old
    if version_a_gt_version_b(version_latest_contract, deployed_contract_version):

        logger.info(f"Deploying {os.path.basename(latest_contract)} version {version_latest_contract}")

        # deploy contract
        if not as_blueprint:
            deployed_contract = boa.load_partial(latest_contract).deploy(*args)
        else:
            deployed_contract = boa.load_partial(latest_contract).deploy_as_blueprint(*args)

        # store abi
        relpath = get_relative_path(contract_folder)
        abi_path = relpath.replace("contracts", "abi")
        abi_file = f".{abi_path}/{os.path.basename(latest_contract).replace('.vy', '.json')}"

        if not os.path.exists(f".{abi_path}"):
            os.makedirs(f".{abi_path}")

        with open(abi_file, "w") as abi_file:
            json.dump(deployed_contract.abi, abi_file, indent=4)
            abi_file.write("\n")

        # update deployment yaml file
        deployment_file.update_contract_deployment(
            contract_folder,
            deployed_contract,
            args,
            as_blueprint=as_blueprint,
        )

    else:
        # return contract object of existing deployment
        logger.info(f"{contract_designation} contract already deployed at {deployed_contract.address}. Fetching ...")
        deployed_contract = boa.load_partial(latest_contract).at(deployed_contract.address)

    return deployed_contract


def deploy_via_create2(contract_file, abi_encoded_ctor="", is_blueprint=False):
    create2deployer = boa.loads_abi(CREATE2DEPLOYER_ABI).at(CREATE2DEPLOYER_ADDRESS)
    contract_obj = boa.load_partial(contract_file)
    compiled_bytecode = contract_obj.compiler_data.bytecode
    deployment_bytecode = compiled_bytecode + abi_encoded_ctor
    if is_blueprint:
        blueprint_preamble = b"\xFE\x71\x00"
        # Add blueprint preamble to disable calling the contract:
        blueprint_bytecode = blueprint_preamble + deployment_bytecode
        # Add code for blueprint deployment:
        len_blueprint_bytecode = len(blueprint_bytecode).to_bytes(2, "big")
        deployment_bytecode = b"\x61" + len_blueprint_bytecode + b"\x3d\x81\x60\x0a\x3d\x39\xf3" + blueprint_preamble

    precomputed_address = create2deployer.computeAddress(CREATE2_SALT, keccak(deployment_bytecode))
    create2deployer.deploy(0, CREATE2_SALT, deployment_bytecode)
    return contract_obj.at(precomputed_address)