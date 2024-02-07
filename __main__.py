import pulumi
import pulumi_awsx as awsx
import pulumi_aws as aws
import pulumi_eks as eks
import pulumi_kubernetes as kubernetes
import json

managed_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
]

assume_role_policy = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Action": "sts:AssumeRole",
        "Effect": "Allow",
        "Principal": {
            "Service": "ec2.amazonaws.com",
        },
    }],
})

role1 = aws.iam.Role("role1",
    assume_role_policy=assume_role_policy,
    managed_policy_arns=managed_policy_arns)

role2 = aws.iam.Role("role2",
    assume_role_policy=assume_role_policy,
    managed_policy_arns=managed_policy_arns)
instance_profile1 = aws.iam.InstanceProfile("instanceProfile1", role=role1.name)
instance_profile2 = aws.iam.InstanceProfile("instanceProfile2", role=role2.name)

# Create a vpc for the cluster
vpc = awsx.ec2.Vpc("vpc")
# Create an EKS cluster with default configuration
cluster = eks.Cluster("cluster",
                      skip_default_node_group=True,
                      instance_roles=None,
                      vpc_id=vpc.vpc_id,
                      public_subnet_ids=vpc.public_subnet_ids,
                      private_subnet_ids=vpc.private_subnet_ids,
                      node_associate_public_ip_address=False,
                      desired_capacity=5,
                      min_size=3,
                      max_size=5,
                      enabled_cluster_log_types=[
                          "api",
                          "audit",
                          "authenticator",
                      ])

fixed_node_group = eks.NodeGroupV2("fixedNodeGroup",
    cluster=cluster,
    instance_type="t2.medium",
    desired_capacity=2,
    min_size=1,
    max_size=3,
    spot_price="1",
    labels={
        "ondemand": "true",
    },
    instance_profile=instance_profile1)

spot_node_group = eks.NodeGroupV2("spotNodeGroup",
    cluster=cluster,
    instance_type="t2.medium",
    desired_capacity=1,
    min_size=1,
    max_size=2,
    labels={
        "preemptible": "true",
    },
    instance_profile=instance_profile2)

eks_provider = kubernetes.Provider("eks-provider", kubeconfig=cluster.kubeconfig_json, enable_server_side_apply=True)
app_name = "my-app"

# Use ECR image
repository = awsx.ecr.Repository("repository",
                                 awsx.ecr.RepositoryArgs(force_delete=True),)

image = awsx.ecr.Image("Image",awsx.ecr.ImageArgs(
    repository_url=repository.url, context="./app", platform="linux/amd64"
),)

# Deploy a small canary service(NGINX), to test that the cluster is working
my_deployment = kubernetes.apps.v1.Deployment("my-deployment",
                                              metadata=kubernetes.meta.v1.ObjectMetaArgs(
                                                  labels={
                                                      "appClass": app_name,
                                                  },
        
                                              ),
                                              spec=kubernetes.apps.v1.DeploymentSpecArgs(
                                                replicas=2,
                                                selector=kubernetes.meta.v1.LabelSelectorArgs(
                                                    match_labels={
                                                        "appClass": app_name
                                                    }
                                                ),
                                                template=kubernetes.core.v1.PodTemplateSpecArgs(
                                                    metadata=kubernetes.meta.v1.ObjectMetaArgs(
                                                        labels={
                                                            "appClass": app_name,
                                                        }
                                                    ),
                                                    spec=kubernetes.core.v1.PodSpecArgs(
                                                        containers=[kubernetes.core.v1.ContainerArgs(
                                                            name=app_name,
                                                            image=image.image_uri,
                                                            ports=[kubernetes.core.v1.ContainerPortArgs(
                                                                name="http",
                                                                container_port=80
                                                            )],
                                                        )],
                                                    ),
                                                ),
                                              ),
                                              opts = pulumi.ResourceOptions(provider=eks_provider)
                                              )
my_service = kubernetes.core.v1.Service("my-service",
                                        metadata=kubernetes.meta.v1.ObjectMetaArgs(
                                            labels={
                                                "appClass": "my-deployment"
                                            }
                                        ),
                                        spec=kubernetes.core.v1.ServiceSpecArgs(
                                            type="LoadBalancer",
                                            ports=[kubernetes.core.v1.ServicePortArgs(
                                                port=80,
                                                target_port="http",
                                            )],
                                            selector={
                                                "appClass": "my-deployment",
                                            }
                                        ),
                                        opts=pulumi.ResourceOptions(provider=eks_provider))

# Deploy helm chart
wordpress = kubernetes.helm.v3.Release("wordpress",
                                       repository_opts=kubernetes.helm.v3.RepositoryOptsArgs(
                                           repo="https://charts.bitnami.com/bitnami",
                                       ),
                                       chart="wordpress",
                                       values={
                                           "wordpressBlogName": "My cool kubernetes blog!"
                                       },
                                       opts=pulumi.ResourceOptions(provider=eks_provider))


# Export the clusters kubeconfig
pulumi.export("kubeconfig", cluster.kubeconfig)
# Export the url of the loadbalanced service
pulumi.export("url", my_service.status.load_balancer.ingress[0].hostname)