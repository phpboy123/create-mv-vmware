from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import os
from dotenv import load_dotenv
import ssl
import time

# Загружаем переменные окружения из .env файла
load_dotenv()

ESXI_HOST = os.getenv('ESXI_HOST')
ESXI_USER = os.getenv('ESXI_USER')
ESXI_PASS = os.getenv('ESXI_PASS')
DATASTORE = os.getenv('DATASTORE')
SIZE_STOREGE = int(os.getenv('SIZE_STOREGE'))
NETWORK = os.getenv('NETWORK')
VM_NAME = os.getenv('VM_NAME')
CD_ROM = os.getenv('CD_ROM')

# Отключение проверки SSL
context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE

# Подключение к ESXi
si = SmartConnect(host=ESXI_HOST, user=ESXI_USER, pwd=ESXI_PASS, sslContext=context)
content = si.RetrieveContent()
datacenter = content.rootFolder.childEntity[0]
vm_folder = datacenter.vmFolder
resource_pool = datacenter.hostFolder.childEntity[0].resourcePool
file_manager = content.fileManager

def create_datastore_folder(si, datastore, vm_name):
    folder_path = f"[{datastore}] {vm_name}/"
    
    try:
        file_manager.MakeDirectory(folder_path, datacenter, True)
        print(f"✅ Folder created: {folder_path}")
        time.sleep(3)  # Ожидание, чтобы ESXi обновил метаданные
    except Exception as e:
        print(f"⚠️ Warning: Unable to create folder (may already exist): {e}")

# Функция создания VMDK
def create_virtual_disk(si, datastore, vm_name):
    content = si.RetrieveContent()
    disk_manager = content.virtualDiskManager

    vmdk_path = f"[{datastore}] {vm_name}/{vm_name}.vmdk"
    
    spec = vim.VirtualDiskManager.FileBackedVirtualDiskSpec()
    spec.adapterType = "lsiLogic"
    spec.diskType = "thin"
    spec.capacityKb = SIZE_STOREGE * 1024 * 1024

    print(f"🔄 Creating virtual disk: {vmdk_path}...")

    task = disk_manager.CreateVirtualDisk(datacenter=None, name=vmdk_path, spec=spec)

    while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
        time.sleep(1)

    if task.info.state == vim.TaskInfo.State.success:
        print(f"✅ Virtual disk created successfully: {vmdk_path}")
        return vmdk_path
    else:
        raise Exception(f"❌ Failed to create virtual disk: {task.info.error}")


# Создаём папку для VM
create_datastore_folder(si, DATASTORE, VM_NAME)

# Создаём VMDK
vmdk_path = create_virtual_disk(si, DATASTORE, VM_NAME)

# Поиск сети
network = None
for net in datacenter.networkFolder.childEntity:
    if net.name == NETWORK:
        network = net
        break

if not network:
    raise Exception(f"❌ Network '{NETWORK}' not found")

# Конфигурация VM
vmx_file = vim.vm.FileInfo(
    logDirectory=None, snapshotDirectory=None, suspendDirectory=None,
    vmPathName=f"[{DATASTORE}] {VM_NAME}/"
)

config = vim.vm.ConfigSpec(
    name=VM_NAME,
    memoryMB=2048,
    numCPUs=2,
    files=vmx_file,
    guestId="ubuntu64Guest",
    version="vmx-13",
    deviceChange=[
        # Контроллер SCSI
        vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
            device=vim.vm.device.VirtualLsiLogicController(
                key=1000,
                busNumber=0,
                sharedBus=vim.vm.device.VirtualSCSIController.Sharing.noSharing
            )
        ),
        # Диск
        vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
            device=vim.vm.device.VirtualDisk(
                key=0,
                controllerKey=1000,
                unitNumber=0,
                capacityInKB=SIZE_STOREGE * 1024 * 1024,
                backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
                    fileName=vmdk_path,  # Используем правильный путь
                    diskMode="persistent",
                    thinProvisioned=True
                )
            )
        ),
        # Сетевой адаптер
        vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
            device=vim.vm.device.VirtualE1000(
                key=0,
                deviceInfo=vim.Description(label="Network Adapter", summary=NETWORK),
                backing=vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(
                    deviceName=NETWORK,
                    network=network
                ),
                addressType="generated"
            )
        ),
        # CD-ROM с ISO
        vim.vm.device.VirtualDeviceSpec(
            operation=vim.vm.device.VirtualDeviceSpec.Operation.add,
            device=vim.vm.device.VirtualCdrom(
                key=0,
                controllerKey=200,
                unitNumber=0,
                backing=vim.vm.device.VirtualCdrom.IsoBackingInfo(
                    fileName=CD_ROM
                ),
                connectable=vim.vm.device.VirtualDevice.ConnectInfo(
                    startConnected=True,
                    connected=True,
                    allowGuestControl=True
                )
            )
        )
    ]
)

# Создание VM
task = vm_folder.CreateVM_Task(config=config, pool=resource_pool)

print(f"🔄 Creating VM '{VM_NAME}'...")

# Ожидание завершения задачи
while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
    time.sleep(1)

if task.info.state == vim.TaskInfo.State.success:
    print(f"✅ VM '{VM_NAME}' created successfully.")
else:
    print(f"❌ Failed to create VM: {task.info.error}")
    Disconnect(si)
    exit()

# Получение объекта VM
vm = [vm for vm in vm_folder.childEntity if vm.name == VM_NAME][0]

# Включаем VM
task = vm.PowerOn()
print(f"🔄 VM '{vm.name}' is powering on...")

while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
    time.sleep(1)

if task.info.state == vim.TaskInfo.State.success:
    print(f"✅ VM '{VM_NAME}' powered on successfully.")
else:
    print(f"❌ Failed to power on VM: {task.info.error}")

# Отключение от ESXi
Disconnect(si)
